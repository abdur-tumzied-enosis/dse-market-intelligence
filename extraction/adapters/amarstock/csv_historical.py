from __future__ import annotations

import asyncio
import io
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import normalize_ticker, to_decimal, to_utc

AMARSTOCK_SCRAPE_BASE = "https://amarstock.com"


class AmarStockCSVAdapter(BaseAdapter):
    """
    AmarStock CSV bulk download — historical OHLCV.

    URL pattern: https://amarstock.com/api/export/csv?tradingCode=SQURPHARMA
    Returns CSV with columns: Date, Open, High, Low, Close, Volume, Value, Trade

    PRIMARY source for historical data (bulk load + incremental).
    Much faster than bdshare get_hist_data() for large date ranges.
    """
    name = "amarstock_csv"
    priority = 1
    timeout_seconds = 60  # CSVs can be large

    def __init__(
        self,
        base_url: str = AMARSTOCK_SCRAPE_BASE,
        request_delay: float = 2.0,
    ) -> None:
        self._base_url = base_url
        self._delay = request_delay

    def normalize(self, raw: pd.DataFrame, ticker: str = "") -> pd.DataFrame:
        """
        AmarStock CSV columns (confirmed from smoke test — update after running):
        Date, Open, High, Low, Close, Volume, Value, Trade
        """
        df = raw.copy()
        df.columns = [c.strip().upper() for c in df.columns]

        def parse_date(val: Any) -> datetime | None:
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%B %d, %Y"):
                try:
                    return to_utc(datetime.strptime(str(val).strip(), fmt), assume_dhaka=True)
                except (ValueError, TypeError):
                    continue
            return None

        out = pd.DataFrame()
        out["ticker"]    = normalize_ticker(ticker)
        out["date"]      = df["DATE"].apply(parse_date)
        out["open"]      = df["OPEN"].apply(to_decimal)
        out["high"]      = df["HIGH"].apply(to_decimal)
        out["low"]       = df["LOW"].apply(to_decimal)
        out["close"]     = df["CLOSE"].apply(to_decimal)
        out["volume"]    = pd.to_numeric(df["VOLUME"], errors="coerce")
        out["trades"]    = pd.to_numeric(df.get("TRADE", pd.Series()), errors="coerce")
        out["value_bdt"] = df.get("VALUE", pd.Series(dtype=object)).apply(to_decimal)
        out["source"]    = self.name
        return out.dropna(subset=["date", "close"]).sort_values("date")

    async def fetch(self, ticker: str, **kwargs: Any) -> AdapterResult:
        ticker = normalize_ticker(ticker)
        url = f"{self._base_url}/api/export/csv?tradingCode={ticker}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                csv_bytes = resp.content
        except httpx.HTTPStatusError as exc:
            raise AdapterError(self.name, f"HTTP {exc.response.status_code} for {ticker}", retryable=True) from exc
        except Exception as exc:
            raise AdapterError(self.name, f"download failed for {ticker}: {exc}", retryable=True) from exc

        if not csv_bytes or len(csv_bytes) < 50:
            raise AdapterError(self.name, f"empty CSV for {ticker}", retryable=False)

        try:
            raw = pd.read_csv(io.StringIO(csv_bytes.decode("utf-8", errors="replace")))
        except Exception as exc:
            raise AdapterError(self.name, f"CSV parse failed for {ticker}: {exc}", retryable=False) from exc

        if len(raw) == 0:
            raise AdapterError(self.name, f"CSV had 0 rows for {ticker}", retryable=False)

        normalized = self.normalize(raw, ticker=ticker)

        await asyncio.sleep(self._delay)  # rate limit

        return AdapterResult(
            data=normalized,
            source_name=self.name,
            fetched_at=datetime.now(timezone.utc),
            quality="ok",
            records=len(normalized),
            raw_sample=raw.iloc[0].to_dict() if len(raw) > 0 else {},
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                url = f"{self._base_url}/api/export/csv?tradingCode=GP"
                resp = await client.get(url)
                return resp.status_code == 200 and len(resp.content) > 100
        except Exception:
            return False
