from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd
from bs4 import BeautifulSoup

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import normalize_ticker, to_decimal

# Old /latest_share_price.php returns 404. Current URL (confirmed 2026-05-21):
LIVE_PRICE_URL = "https://www.dsebd.org/latest_share_price_scroll_l.php"

# Confirmed table headers (2026-05-21):
# ['#', 'TRADING CODE', 'LTP*', 'HIGH', 'LOW', 'CLOSEP*', 'YCP*', 'CHANGE', 'TRADE', 'VALUE (mn)', 'VOLUME']
# No OPEN, no %CHANGE column. LTP* = last traded price. CLOSEP* = session close. YCP* = yesterday close.

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

# Raw header text (uppercased, stripped) → canonical column name
_COL_MAP: dict[str, str] = {
    "TRADING CODE": "ticker",
    "#":            "_row_num",   # discard
    "LTP*":         "close",      # last traded price = effective close
    "HIGH":         "high",
    "LOW":          "low",
    "CLOSEP*":      "close_official",  # official close price (same as LTP during session)
    "YCP*":         "prev_close",      # yesterday close price
    "CHANGE":       "change",
    "TRADE":        "trades",
    "VALUE (MN)":   "value_mn",
    "VOLUME":       "volume",
}


class DSEDirectLivePricesAdapter(BaseAdapter):
    """
    DSE official site — all-stock live prices.

    URL: https://www.dsebd.org/latest_share_price_scroll_l.php
    Method: HTTP + BeautifulSoup HTML table scrape (no JS required).
    Priority 3 — activates only if both bdshare AND AmarStock fail.

    Confirmed headers (2026-05-21):
        #, TRADING CODE, LTP*, HIGH, LOW, CLOSEP*, YCP*, CHANGE, TRADE, VALUE (mn), VOLUME
    Note: No OPEN price, no %CHANGE in this feed.
    """
    name = "dse_direct_live_prices"
    priority = 3
    timeout_seconds = 30

    def __init__(self, url: str = LIVE_PRICE_URL) -> None:
        self._url = url

    def _parse_html(self, html: str) -> pd.DataFrame:
        soup = BeautifulSoup(html, "lxml")

        # Find the stock table — confirmed has 11 columns with 'TRADING CODE' header
        table = None
        for t in soup.find_all("table"):
            ths = [th.get_text(strip=True).upper() for th in t.find_all("th")]
            if "TRADING CODE" in ths and len(ths) >= 8:
                table = t
                break

        if table is None:
            raise ValueError("no stock table found (expected 'TRADING CODE' header)")

        raw_headers = [th.get_text(strip=True).upper() for th in table.find_all("th")]

        # DSE HTML is malformed: <tbody> holds only 1 row; remaining 400+ rows
        # are direct <table> children. Use table.find_all("tr") to get everything,
        # then skip any <tr> that contains only <th> cells (header rows).
        all_trs = table.find_all("tr")

        rows = []
        for tr in all_trs:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if cells and len(cells) >= len(raw_headers) - 1:
                rows.append(dict(zip(raw_headers, cells + [""] * max(0, len(raw_headers) - len(cells)))))

        if not rows:
            raise ValueError("table found but no data rows extracted")

        return pd.DataFrame(rows)

    def normalize(self, raw: pd.DataFrame) -> pd.DataFrame:
        df = raw.copy()
        # Rename using map; strip trailing * from headers if not already done
        df.rename(columns=_COL_MAP, inplace=True)

        if "ticker" not in df.columns:
            raise ValueError(f"no ticker column after rename; columns={list(df.columns)}")

        out = pd.DataFrame()
        out["ticker"] = df["ticker"].apply(normalize_ticker)

        # Numeric price columns
        for src in ("close", "high", "low", "close_official", "prev_close", "change"):
            if src in df.columns:
                out[src] = df[src].apply(to_decimal)
            else:
                out[src] = None

        # Integer-ish columns with commas
        for src, dst in (("volume", "volume"), ("trades", "trades")):
            if src in df.columns:
                out[dst] = pd.to_numeric(
                    df[src].astype(str).str.replace(",", "", regex=False),
                    errors="coerce",
                )
            else:
                out[dst] = None

        out["value_mn"] = df["value_mn"].apply(to_decimal) if "value_mn" in df.columns else None
        out["open"] = None          # not provided in this feed
        out["change_pct"] = None    # not provided in this feed
        out["fetched_at"] = datetime.now(timezone.utc)
        out["source"] = self.name
        return out.loc[out["ticker"].str.len() > 0]  # drop blank rows

    async def fetch(self, **kwargs: Any) -> AdapterResult:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                headers=HEADERS,
                follow_redirects=True,
            ) as client:
                resp = await client.get(self._url)
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AdapterError(self.name, f"HTTP {exc.response.status_code}", retryable=True) from exc
        except Exception as exc:
            raise AdapterError(self.name, f"request failed: {exc}", retryable=True) from exc

        try:
            raw = self._parse_html(resp.text)
        except Exception as exc:
            raise AdapterError(self.name, f"HTML parse failed: {exc}", retryable=False) from exc

        if raw.empty:
            raise AdapterError(self.name, "parsed empty table", retryable=True)

        try:
            normalized = self.normalize(raw)
        except Exception as exc:
            raise AdapterError(self.name, f"normalize failed: {exc}", retryable=False) from exc

        return AdapterResult(
            data=normalized,
            source_name=self.name,
            fetched_at=datetime.now(timezone.utc),
            quality="partial",   # no OPEN or %CHANGE — caller should note
            records=len(normalized),
            raw_sample=raw.iloc[0].to_dict(),
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10, headers=HEADERS, follow_redirects=True) as c:
                resp = await c.get(self._url)
                return resp.status_code == 200 and b"TRADING CODE" in resp.content
        except Exception:
            return False
