from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import normalize_ticker, to_decimal


class BDShareLivePricesAdapter(BaseAdapter):
    name = "bdshare_live_prices"
    priority = 2
    timeout_seconds = 20

    def normalize(self, raw: pd.DataFrame) -> pd.DataFrame:
        """
        bdshare get_current_trade_data() actual columns (verified 2026-05-21):
        symbol, ltp, high, low, close, ycp, change, trade, value, volume

        No open price in live feed. ltp=last traded price, ycp=yesterday close.
        """
        df = raw.copy()
        df.columns = [c.strip().upper() for c in df.columns]

        out = pd.DataFrame()
        out["ticker"]      = df["SYMBOL"].apply(normalize_ticker)
        out["open"]        = None                                # not in live feed
        out["high"]        = df["HIGH"].apply(to_decimal)
        out["low"]         = df["LOW"].apply(to_decimal)
        out["close"]       = df["LTP"].apply(to_decimal)         # last traded price
        out["prev_close"]  = df["YCP"].apply(to_decimal)         # yesterday close
        out["change_pct"]  = df["CHANGE"].apply(to_decimal)
        out["volume"]      = pd.to_numeric(df["VOLUME"], errors="coerce")
        out["trades"]      = pd.to_numeric(df["TRADE"], errors="coerce")
        out["value_bdt"]   = df["VALUE"].apply(to_decimal)
        out["fetched_at"]  = datetime.now(timezone.utc)
        out["source"]      = self.name
        return out

    async def fetch(self, **kwargs: Any) -> AdapterResult:
        try:
            import bdshare as bd
        except ImportError as exc:
            raise AdapterError(self.name, "bdshare not installed", retryable=False) from exc

        try:
            raw: pd.DataFrame = bd.get_current_trade_data()
        except Exception as exc:
            raise AdapterError(self.name, f"get_current_trade_data failed: {exc}") from exc

        if raw is None or len(raw) == 0:
            raise AdapterError(self.name, "returned empty DataFrame", retryable=True)

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
            raw = bd.get_current_trade_data()
            return raw is not None and len(raw) > 0
        except Exception:
            return False
