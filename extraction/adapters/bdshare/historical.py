from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import normalize_ticker, to_decimal, to_utc


class BDShareHistoricalAdapter(BaseAdapter):
    """
    bdshare get_hist_data(ticker, start, end) — daily OHLCV for one ticker.
    Used for incremental updates (last 7 days). Bulk historical load uses AmarStock CSV.

    bdshare column names (from smoke test, may vary):
    DATE, TRADING CODE, OPEN, HIGH, LOW, CLOSEP, VOLUME, TRADE, VALUE
    """
    name = "bdshare_historical"
    priority = 2
    timeout_seconds = 30

    def normalize(self, raw: pd.DataFrame) -> pd.DataFrame:
        df = raw.copy()
        df.columns = [c.strip().upper() for c in df.columns]

        # DATE column is typically 'YYYY-MM-DD' string or datetime
        def parse_date(val: Any) -> datetime | None:
            if isinstance(val, (datetime, pd.Timestamp)):
                return to_utc(pd.Timestamp(val).to_pydatetime(), assume_dhaka=True)
            try:
                return to_utc(datetime.strptime(str(val).strip(), "%Y-%m-%d"), assume_dhaka=True)
            except (ValueError, TypeError):
                return None

        out = pd.DataFrame()
        ticker_col = "TRADING CODE" if "TRADING CODE" in df.columns else df.columns[1]
        out["ticker"]     = df[ticker_col].apply(normalize_ticker)
        out["date"]       = df["DATE"].apply(parse_date)
        out["open"]       = df["OPEN"].apply(to_decimal)
        out["high"]       = df["HIGH"].apply(to_decimal)
        out["low"]        = df["LOW"].apply(to_decimal)
        out["close"]      = df["CLOSEP"].apply(to_decimal)
        out["volume"]     = pd.to_numeric(df["VOLUME"], errors="coerce")
        out["trades"]     = pd.to_numeric(df.get("TRADE", pd.Series()), errors="coerce")
        out["value_bdt"]  = df.get("VALUE", pd.Series(dtype=object)).apply(to_decimal)
        out["source"]     = self.name
        return out.dropna(subset=["date", "close"])

    async def fetch(self, ticker: str, start: str, end: str, **kwargs: Any) -> AdapterResult:
        """
        Args:
            ticker: DSE ticker symbol (e.g. 'SQURPHARMA')
            start:  ISO date string 'YYYY-MM-DD'
            end:    ISO date string 'YYYY-MM-DD'
        """
        try:
            import bdshare as bd
        except ImportError as exc:
            raise AdapterError(self.name, "bdshare not installed", retryable=False) from exc

        try:
            raw: pd.DataFrame = bd.get_hist_data(normalize_ticker(ticker), start, end)
        except Exception as exc:
            raise AdapterError(self.name, f"get_hist_data({ticker}) failed: {exc}") from exc

        if raw is None or len(raw) == 0:
            raise AdapterError(self.name, f"no data for {ticker} {start}–{end}", retryable=True)

        normalized = self.normalize(raw)
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
            import bdshare as bd
            end = date.today().isoformat()
            start = end  # single day
            raw = bd.get_hist_data("SQURPHARMA", start, end)
            return raw is not None
        except Exception:
            return False
