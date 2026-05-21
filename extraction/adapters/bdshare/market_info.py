from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import to_decimal


class BDShareMarketInfoAdapter(BaseAdapter):
    """
    bdshare get_market_info() — DSEX, DS30, DSES index snapshot.

    Actual columns (verified 2026-05-21): Date, Total Trade, Total Volume,
    Total Value (mn), Total Market Cap. (mn), DSEX Index, DSES Index, DS30 Index, DGEN Index
    Returns 30-day history; we take row 0 (most recent trading day).
    No change_pct column — not available from this endpoint.
    """
    name = "bdshare_market_info"
    priority = 1
    timeout_seconds = 15

    def normalize(self, raw: pd.DataFrame) -> pd.DataFrame:
        df = raw.copy()
        df.columns = [c.strip().upper() for c in df.columns]

        latest = df.iloc[0]
        fetched = datetime.now(timezone.utc)

        index_col_map = {
            "DSEX": "DSEX INDEX",
            "DS30": "DS30 INDEX",
            "DSES": "DSES INDEX",
        }
        rows = []
        for idx_name, col in index_col_map.items():
            if col not in df.columns:
                continue
            rows.append({
                "index_name": idx_name,
                "value":      to_decimal(latest[col]),
                "change_pct": None,
                "fetched_at": fetched,
                "source":     self.name,
            })
        return pd.DataFrame(rows)

    async def fetch(self, **kwargs: Any) -> AdapterResult:
        try:
            import bdshare as bd
        except ImportError as exc:
            raise AdapterError(self.name, "bdshare not installed", retryable=False) from exc

        try:
            raw: pd.DataFrame = bd.get_market_info()
        except Exception as exc:
            raise AdapterError(self.name, f"get_market_info failed: {exc}") from exc

        if raw is None or len(raw) == 0:
            raise AdapterError(self.name, "returned empty", retryable=True)

        normalized = self.normalize(raw)
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
            import bdshare as bd
            raw = bd.get_market_info()
            return raw is not None and len(raw) > 0
        except Exception:
            return False
