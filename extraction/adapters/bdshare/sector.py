from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import to_decimal


class BDShareSectorAdapter(BaseAdapter):
    """
    bdshare get_sector_performance() — sector-level summary.
    Expected columns: SECTOR, CHANGE_PCT (or similar), PE, MARKET_CAP
    """
    name = "bdshare_sector"
    priority = 1
    timeout_seconds = 15

    def normalize(self, raw: pd.DataFrame) -> pd.DataFrame:
        df = raw.copy()
        df.columns = [c.strip().upper().replace(" ", "_") for c in df.columns]

        out = pd.DataFrame()
        sector_col = next((c for c in df.columns if "SECTOR" in c), df.columns[0])
        out["sector"] = df[sector_col].str.strip()

        change_col = next((c for c in df.columns if "CHANGE" in c), None)
        out["change_pct"] = df[change_col].apply(to_decimal) if change_col else None

        pe_col = next((c for c in df.columns if c in ("PE", "P/E", "P_E")), None)
        out["pe"] = df[pe_col].apply(to_decimal) if pe_col else None

        cap_col = next((c for c in df.columns if "MARKET" in c or "CAP" in c), None)
        out["market_cap_bdt"] = df[cap_col].apply(to_decimal) if cap_col else None

        out["fetched_at"] = datetime.now(timezone.utc)
        out["source"]     = self.name
        return out

    async def fetch(self, **kwargs: Any) -> AdapterResult:
        try:
            import bdshare as bd
        except ImportError as exc:
            raise AdapterError(self.name, "bdshare not installed", retryable=False) from exc

        try:
            raw: pd.DataFrame = bd.get_sector_performance()
        except Exception as exc:
            raise AdapterError(self.name, f"get_sector_performance failed: {exc}") from exc

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
            raw = bd.get_sector_performance()
            return raw is not None and len(raw) > 0
        except Exception:
            return False
