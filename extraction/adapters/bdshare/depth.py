from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import normalize_ticker, to_decimal


class BDShareDepthAdapter(BaseAdapter):
    """
    bdshare get_market_depth_data(ticker) — 5-level order book.

    Returns two tables: buy side + sell side, each with price + volume.
    Canonical output: one row per ticker with bid/ask_price_1..5, bid/ask_vol_1..5
    """
    name = "bdshare_depth"
    priority = 1
    timeout_seconds = 15

    def normalize(self, raw: list[pd.DataFrame] | pd.DataFrame, ticker: str = "") -> pd.DataFrame:
        """
        raw is either:
        - A list [buy_df, sell_df]  (each 5 rows: price, volume)
        - A single DataFrame with all columns (confirmed by smoke test)
        """
        result: dict[str, Any] = {
            "ticker":     normalize_ticker(ticker),
            "fetched_at": datetime.now(timezone.utc),
            "source":     self.name,
        }

        if isinstance(raw, list) and len(raw) >= 2:
            buy_df, sell_df = raw[0], raw[1]
            for i, (_, row) in enumerate(buy_df.iterrows(), 1):
                if i > 5:
                    break
                cols = list(row.index)
                result[f"bid_price_{i}"] = to_decimal(row.iloc[0])
                result[f"bid_vol_{i}"]   = to_decimal(row.iloc[1]) if len(cols) > 1 else None
            for i, (_, row) in enumerate(sell_df.iterrows(), 1):
                if i > 5:
                    break
                result[f"ask_price_{i}"] = to_decimal(row.iloc[0])
                result[f"ask_vol_{i}"]   = to_decimal(row.iloc[1]) if len(cols) > 1 else None

        elif isinstance(raw, pd.DataFrame):
            df = raw.copy()
            df.columns = [c.strip().upper() for c in df.columns]
            for i in range(1, 6):
                for side, prefix in (("BID", "bid"), ("ASK", "ask")):
                    p_col = f"{side}_PRICE_{i}"
                    v_col = f"{side}_VOL_{i}"
                    if p_col in df.columns:
                        result[f"{prefix}_price_{i}"] = to_decimal(df[p_col].iloc[0])
                    if v_col in df.columns:
                        result[f"{prefix}_vol_{i}"] = to_decimal(df[v_col].iloc[0])

        return pd.DataFrame([result])

    async def fetch(self, ticker: str, **kwargs: Any) -> AdapterResult:
        try:
            import bdshare as bd
        except ImportError as exc:
            raise AdapterError(self.name, "bdshare not installed", retryable=False) from exc

        ticker = normalize_ticker(ticker)
        try:
            raw = bd.get_market_depth_data(ticker)
        except Exception as exc:
            raise AdapterError(self.name, f"get_market_depth_data({ticker}) failed: {exc}") from exc

        if raw is None:
            raise AdapterError(self.name, f"no depth data for {ticker}", retryable=True)

        normalized = self.normalize(raw, ticker=ticker)
        sample: dict[str, Any]
        if isinstance(raw, list):
            sample = {"buy_rows": len(raw[0]), "sell_rows": len(raw[1])}
        else:
            sample = raw.iloc[0].to_dict() if len(raw) > 0 else {}

        return AdapterResult(
            data=normalized,
            source_name=self.name,
            fetched_at=datetime.now(timezone.utc),
            quality="ok",
            records=len(normalized),
            raw_sample=sample,
        )

    async def health_check(self) -> bool:
        try:
            import bdshare as bd
            raw = bd.get_market_depth_data("GP")
            return raw is not None
        except Exception:
            return False
