from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

import httpx
import pandas as pd
from bs4 import BeautifulSoup

from extraction.adapters.dse_direct._tls import dse_client
from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import normalize_ticker, to_decimal

GAINERS_URL = "https://www.dsebd.org/top_ten_gainer.php"
LOSERS_URL  = "https://www.dsebd.org/top_ten_loser.php"

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

# Confirmed columns (2026-05-23): #, TRADING CODE, CLOSEP*, HIGH, LOW, YCP*, % CHANGE
_COL_MAP: dict[str, str] = {
    "#":             "rank",
    "TRADING CODE":  "ticker",
    "CLOSEP*":       "close",
    "HIGH":          "high",
    "LOW":           "low",
    "YCP*":          "prev_close",
    "% CHANGE":      "change_pct",
}


def _parse_table(html: str, url: str) -> pd.DataFrame:
    soup = BeautifulSoup(html, "lxml")
    table = None
    for t in soup.find_all("table"):
        ths = [th.get_text(strip=True).upper() for th in t.find_all("th")]
        if "TRADING CODE" in ths and "% CHANGE" in ths:
            table = t
            break
    if table is None:
        raise ValueError(f"no gainer/loser table found at {url}")

    raw_headers = [th.get_text(strip=True).upper() for th in table.find_all("th")]
    rows = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if cells and len(cells) >= 4:
            rows.append(dict(zip(raw_headers, cells + [""] * max(0, len(raw_headers) - len(cells)))))
    if not rows:
        raise ValueError("table found but no data rows")
    return pd.DataFrame(rows)


def _normalize(raw: pd.DataFrame, direction: Literal["gainer", "loser"], adapter_name: str) -> pd.DataFrame:
    df = raw.copy()
    df.rename(columns=_COL_MAP, inplace=True)
    if "ticker" not in df.columns:
        raise ValueError(f"no ticker column; got {list(df.columns)}")

    out = pd.DataFrame()
    out["rank"]       = pd.to_numeric(df.get("rank"), errors="coerce").astype("Int64")
    out["ticker"]     = df["ticker"].apply(normalize_ticker)
    out["close"]      = df.get("close", pd.Series(dtype=str)).apply(to_decimal)
    out["high"]       = df.get("high",  pd.Series(dtype=str)).apply(to_decimal)
    out["low"]        = df.get("low",   pd.Series(dtype=str)).apply(to_decimal)
    out["prev_close"] = df.get("prev_close", pd.Series(dtype=str)).apply(to_decimal)
    out["change_pct"] = df.get("change_pct", pd.Series(dtype=str)).apply(to_decimal)
    out["direction"]  = direction
    out["fetched_at"] = datetime.now(timezone.utc)
    out["source"]     = adapter_name
    return out.loc[out["ticker"].str.len() > 0]


class DSEDirectGainersAdapter(BaseAdapter):
    """
    DSE official site — top 10 gainers by % change.
    URL: https://www.dsebd.org/top_ten_gainer.php
    Method: HTTP + BeautifulSoup (no JS). Confirmed working 2026-05-23.
    """
    name = "dse_direct_gainers"
    priority = 1
    timeout_seconds = 20

    def normalize(self, raw: pd.DataFrame) -> pd.DataFrame:
        return _normalize(raw, "gainer", self.name)

    async def fetch(self, **kwargs: Any) -> AdapterResult:
        try:
            async with dse_client(timeout=self.timeout_seconds, headers=HEADERS, follow_redirects=True) as c:
                resp = await c.get(GAINERS_URL)
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AdapterError(self.name, f"HTTP {exc.response.status_code}", retryable=True) from exc
        except Exception as exc:
            raise AdapterError(self.name, f"request failed: {exc}", retryable=True) from exc

        try:
            raw = _parse_table(resp.text, GAINERS_URL)
        except Exception as exc:
            raise AdapterError(self.name, f"parse failed: {exc}", retryable=False) from exc

        if raw.empty:
            raise AdapterError(self.name, "empty table", retryable=True)

        normalized = _normalize(raw, "gainer", self.name)
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
            async with dse_client(timeout=10, headers=HEADERS, follow_redirects=True) as c:
                resp = await c.get(GAINERS_URL)
                return resp.status_code == 200 and b"TRADING CODE" in resp.content
        except Exception:
            return False


class DSEDirectLosersAdapter(BaseAdapter):
    """
    DSE official site — top 10 losers by % change.
    URL: https://www.dsebd.org/top_ten_loser.php
    Method: HTTP + BeautifulSoup (no JS). Confirmed working 2026-05-23.
    """
    name = "dse_direct_losers"
    priority = 1
    timeout_seconds = 20

    def normalize(self, raw: pd.DataFrame) -> pd.DataFrame:
        return _normalize(raw, "loser", self.name)

    async def fetch(self, **kwargs: Any) -> AdapterResult:
        try:
            async with dse_client(timeout=self.timeout_seconds, headers=HEADERS, follow_redirects=True) as c:
                resp = await c.get(LOSERS_URL)
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AdapterError(self.name, f"HTTP {exc.response.status_code}", retryable=True) from exc
        except Exception as exc:
            raise AdapterError(self.name, f"request failed: {exc}", retryable=True) from exc

        try:
            raw = _parse_table(resp.text, LOSERS_URL)
        except Exception as exc:
            raise AdapterError(self.name, f"parse failed: {exc}", retryable=False) from exc

        if raw.empty:
            raise AdapterError(self.name, "empty table", retryable=True)

        normalized = _normalize(raw, "loser", self.name)
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
            async with dse_client(timeout=10, headers=HEADERS, follow_redirects=True) as c:
                resp = await c.get(LOSERS_URL)
                return resp.status_code == 200 and b"TRADING CODE" in resp.content
        except Exception:
            return False
