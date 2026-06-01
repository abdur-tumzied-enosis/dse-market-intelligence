from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd
from bs4 import BeautifulSoup

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import to_decimal

MARKET_INFO_URL = "https://www.dsebd.org/index.php"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.dsebd.org/",
}

# Confirmed 2026-06-01. The homepage market box (selector
# body > div.containbox > section > div.row.white > div:nth-child(1), which
# renders as div.LeftColHome) holds one .midrow per index:
#   .m_col-1 name | .m_col-2 value | .m_col-3 abs change | .m_col-4 pct | .m_col-5 arrow img
# Index name markup splits the middle letter into a <font> tag, so get_text
# yields "DSEXIndex"/"DSESIndex"/"DS30Index" → strip "INDEX" → DSEX/DSES/DS30.
# The pct text is unsigned; sign comes from the up/down arrow image.
_VALID_INDICES = {"DSEX", "DSES", "DS30"}


def _parse_market_info(html: str) -> pd.DataFrame:
    soup = BeautifulSoup(html, "lxml")

    container = soup.select_one(
        "body > div.containbox > section > div.row.white > div:nth-child(1)"
    )
    if container is None:
        container = soup.find("div", class_="LeftColHome")
    if container is None:
        raise ValueError("market info container not found")

    rows = []
    for mr in container.find_all("div", class_="midrow"):
        c1 = mr.find("div", class_="m_col-1")
        c2 = mr.find("div", class_="m_col-2")
        if c1 is None or c2 is None:
            continue
        name = c1.get_text(strip=True).upper().replace("INDEX", "").strip()
        if name not in _VALID_INDICES:
            continue
        c4 = mr.find("div", class_="m_col-4")
        c5 = mr.find("div", class_="m_col-5")
        arrow = ""
        if c5 is not None:
            img = c5.find("img")
            if img is not None and img.get("src"):
                arrow = str(img["src"])
        rows.append({
            "index_name": name,
            "value":      c2.get_text(strip=True),
            "change_pct": c4.get_text(strip=True) if c4 is not None else "",
            "arrow":      arrow,
        })

    if not rows:
        raise ValueError("no index rows parsed (expected DSEX/DSES/DS30 midrows)")
    return pd.DataFrame(rows)


class DSEDirectMarketInfoAdapter(BaseAdapter):
    """
    DSE official site — DSEX, DSES, DS30 index snapshot from the homepage box.

    URL: https://www.dsebd.org/index.php
    Method: HTTP + BeautifulSoup (no JS required). Confirmed 2026-06-01.
    Priority 1 — primary for the market_indices stream (more reliable than
    bdshare, which is fragile — see docs/bdshare-issues.md).

    Unlike bdshare get_market_info, this source carries change_pct directly
    (derived from the unsigned pct text + up/down arrow image).
    Output schema matches BDShareMarketInfoAdapter:
        index_name, value, change_pct, fetched_at, source
    """
    name = "dse_direct_market_info"
    priority = 1
    timeout_seconds = 20

    def __init__(self, url: str = MARKET_INFO_URL) -> None:
        self._url = url

    def normalize(self, raw: pd.DataFrame) -> pd.DataFrame:
        fetched = datetime.now(timezone.utc)
        out = []
        for _, r in raw.iterrows():
            change_pct = to_decimal(r.get("change_pct"))
            if change_pct is not None and "down" in str(r.get("arrow", "")).lower():
                change_pct = -change_pct
            out.append({
                "index_name": r["index_name"],
                "value":      to_decimal(r["value"]),
                "change_pct": change_pct,
                "fetched_at": fetched,
                "source":     self.name,
            })
        return pd.DataFrame(out)

    async def fetch(self, **kwargs: Any) -> AdapterResult:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, headers=HEADERS, follow_redirects=True
            ) as client:
                resp = await client.get(self._url)
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AdapterError(self.name, f"HTTP {exc.response.status_code}", retryable=True) from exc
        except Exception as exc:
            raise AdapterError(self.name, f"request failed: {exc}", retryable=True) from exc

        try:
            raw = _parse_market_info(resp.text)
        except Exception as exc:
            raise AdapterError(self.name, f"parse failed: {exc}", retryable=False) from exc

        normalized = self.normalize(raw)
        if normalized.empty:
            raise AdapterError(self.name, "no indices after normalize", retryable=True)

        return AdapterResult(
            data=normalized,
            source_name=self.name,
            fetched_at=datetime.now(timezone.utc),
            quality="ok",
            records=len(normalized),
            raw_sample=raw.iloc[0].to_dict(),
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10, headers=HEADERS, follow_redirects=True) as c:
                resp = await c.get(self._url)
                return resp.status_code == 200 and b"m_col-1" in resp.content
        except Exception:
            return False
