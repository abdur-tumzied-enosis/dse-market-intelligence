# extraction/adapters/dse_direct/market_status.py
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd
from bs4 import BeautifulSoup

from extraction.adapters.dse_direct._tls import dse_client
from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.market_status import normalize_status

MARKET_STATUS_URL = "https://www.dsebd.org/index.php"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.dsebd.org/",
}

# The homepage carries a literal "Market Status: Open|Closed" string (confirmed
# 2026-06-02). Match it against the flattened page text so we don't depend on a
# fragile CSS path. The live clock line ("... 2:31 PM Closed") lacks the
# "Market Status" prefix, so it can't false-match.
_STATUS_RE = re.compile(r"market\s*status\s*:?\s*(open|closed)", re.IGNORECASE)


def _parse_status(html: str) -> dict[str, str]:
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    m = _STATUS_RE.search(text)
    if not m:
        raise ValueError("'Market Status' not found in page")
    return {"raw_label": m.group(0).strip(), "status": normalize_status(m.group(0))}


class DSEMarketStatusAdapter(BaseAdapter):
    """DSE official site — trading-session Open/Closed flag from the homepage.

    URL: https://www.dsebd.org/index.php
    Method: HTTP + BeautifulSoup (no JS). Priority 1, no fallback adapter —
    the market_status store handles scrape failure with a clock fallback.
    Output schema: status, raw_label, fetched_at, source.
    """
    name = "dse_direct_market_status"
    priority = 1
    timeout_seconds = 20

    def __init__(self, url: str = MARKET_STATUS_URL) -> None:
        self._url = url

    def normalize(self, raw: pd.DataFrame) -> pd.DataFrame:  # pragma: no cover - trivial
        return raw

    async def fetch(self, **kwargs: Any) -> AdapterResult:
        try:
            async with dse_client(
                timeout=self.timeout_seconds, headers=HEADERS, follow_redirects=True
            ) as client:
                resp = await client.get(self._url)
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AdapterError(self.name, f"HTTP {exc.response.status_code}", retryable=True) from exc
        except Exception as exc:
            raise AdapterError(self.name, f"request failed: {exc}", retryable=True) from exc

        try:
            rec = _parse_status(resp.text)
        except Exception as exc:
            raise AdapterError(self.name, f"parse failed: {exc}", retryable=False) from exc

        fetched = datetime.now(timezone.utc)
        df = pd.DataFrame([{
            "status": rec["status"],
            "raw_label": rec["raw_label"],
            "fetched_at": fetched,
            "source": self.name,
        }])
        return AdapterResult(
            data=df,
            source_name=self.name,
            fetched_at=fetched,
            quality="ok",
            records=1,
            raw_sample=rec,
        )

    async def health_check(self) -> bool:
        try:
            async with dse_client(timeout=10, headers=HEADERS, follow_redirects=True) as c:
                resp = await c.get(self._url)
                return resp.status_code == 200 and b"Market Status" in resp.content
        except Exception:
            return False
