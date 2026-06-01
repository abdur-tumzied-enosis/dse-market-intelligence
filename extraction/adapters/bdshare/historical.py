from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import normalize_ticker, to_decimal


class BDShareHistoricalAdapter(BaseAdapter):
    """
    bdshare get_hist_data(start, end, code) — daily OHLCV for one ticker.
    Used for incremental updates (last 7 days). Bulk historical load uses AmarStock CSV.

    Actual columns (verified 2026-05-21): date index, symbol, ltp, high, low, open, close, ycp, trade, value, volume
    Signature: get_hist_data(start, end, code) NOT (code, start, end).
    """
    name = "bdshare_historical"
    priority = 2
    timeout_seconds = 30

    def normalize(self, raw: pd.DataFrame) -> pd.DataFrame:
        # date is the DataFrame index; reset to make it a column
        df = raw.reset_index()
        df.columns = [c.strip().upper() for c in df.columns]

        def parse_date(val: Any) -> datetime | None:
            # Daily bars carry a pure trading date with no intraday time. Store it at
            # midnight UTC so the calendar date == trading date and rows align with the
            # amarstock_csv convention. assume_dhaka here would shift midnight back 6h
            # into the previous UTC day, splitting one session across two daily buckets.
            if isinstance(val, (datetime, pd.Timestamp)):
                d = pd.Timestamp(val).date()
            else:
                try:
                    d = datetime.strptime(str(val).strip(), "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    return None
            return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)

        out = pd.DataFrame()
        out["ticker"]     = df["SYMBOL"].apply(normalize_ticker)
        out["date"]       = df["DATE"].apply(parse_date)
        out["open"]       = df["OPEN"].apply(to_decimal)
        out["high"]       = df["HIGH"].apply(to_decimal)
        out["low"]        = df["LOW"].apply(to_decimal)
        out["close"]      = df["CLOSE"].apply(to_decimal)
        out["volume"]     = pd.to_numeric(df["VOLUME"], errors="coerce")
        out["trades"]     = pd.to_numeric(df.get("TRADE", pd.Series()), errors="coerce")
        out["value_bdt"]  = df.get("VALUE", pd.Series(dtype=object)).apply(to_decimal)
        out["source"]     = self.name
        out = out.dropna(subset=["date", "close"])

        # Drop no-trade placeholder bars. For illiquid scrips (mostly bonds) bdshare emits
        # a flat row with open=high=low=0, volume=0 and close carried from the prior day.
        # amarstock omits these days entirely; keeping them injects zero-price bars that
        # corrupt OHLC charts and indicators.
        out = out[(out["high"] > 0) & (out["open"] > 0) & (out["low"] > 0)]
        return out

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
            raw: pd.DataFrame = bd.get_historical_data(start, end, normalize_ticker(ticker))
        except Exception as exc:
            raise AdapterError(self.name, f"get_historical_data({ticker}) failed: {exc}") from exc

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
            raw = bd.get_historical_data(start, end, "SQURPHARMA")
            return raw is not None
        except Exception:
            return False
