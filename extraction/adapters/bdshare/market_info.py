from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import to_decimal


class BDShareMarketInfoAdapter(BaseAdapter):
    """
    bdshare get_market_info() — DSEX, DS30, DSES index snapshot.

    Typical columns: DSEX, DSEX_CHANGE, DS30, DS30_CHANGE, DSES, DSES_CHANGE
    (exact names confirmed by smoke test — may differ)
    """
    name = "bdshare_market_info"
    priority = 1
    timeout_seconds = 15

    def normalize(self, raw: pd.DataFrame) -> pd.DataFrame:
        df = raw.copy()
        df.columns = [c.strip().upper() for c in df.columns]

        rows = []
        index_map = {
            "DSEX":  ("DSEX",  "DSEX_CHANGE"),
            "DS30":  ("DS30",  "DS30_CHANGE"),
            "DSES":  ("DSES",  "DSES_CHANGE"),
        }
        fetched = datetime.now(timezone.utc)
        for idx_name, (val_col, chg_col) in index_map.items():
            if val_col not in df.columns:
                continue
            rows.append({
                "index_name": idx_name,
                "value":      to_decimal(df[val_col].iloc[0]),
                "change_pct": to_decimal(df[chg_col].iloc[0]) if chg_col in df.columns else None,
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
