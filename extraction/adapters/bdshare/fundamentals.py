from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import normalize_ticker, to_decimal


class BDShareCompanyInfoAdapter(BaseAdapter):
    """
    bdshare get_company_info(ticker) returns a list of DataFrames.
    Structure varies by company but typically:
      [0] = price/trading info
      [1] = financial ratios (EPS, PE, NAV)
      [2] = shareholding breakdown
      [3] = dividend history (recent)

    This is a secondary source — AmarStockFundamentalsAdapter is primary.
    Falls back here when AmarStock is unavailable.
    """
    name = "bdshare_company_info"
    priority = 2
    timeout_seconds = 20

    def normalize(self, raw: list[pd.DataFrame], ticker: str = "") -> pd.DataFrame:
        result: dict[str, Any] = {
            "ticker":     normalize_ticker(ticker),
            "fetched_at": datetime.now(timezone.utc),
            "source":     self.name,
        }

        # DF[1] — financial ratios
        if len(raw) > 1 and raw[1] is not None and len(raw[1]) > 0:
            ratio_df = raw[1].copy()
            ratio_df.columns = [c.strip().upper() for c in ratio_df.columns]
            row = ratio_df.iloc[0]
            for col in ratio_df.columns:
                col_up = col.upper()
                if "EPS" in col_up:
                    result["eps"] = to_decimal(row[col])
                elif col_up in ("PE", "P/E"):
                    result["pe"] = to_decimal(row[col])
                elif "NAV" in col_up:
                    result["nav"] = to_decimal(row[col])

        # DF[2] — shareholding
        if len(raw) > 2 and raw[2] is not None and len(raw[2]) > 0:
            hold_df = raw[2].copy()
            hold_df.columns = [c.strip().upper() for c in hold_df.columns]
            for _, row in hold_df.iterrows():
                label = str(row.iloc[0]).upper() if len(row) > 0 else ""
                value = row.iloc[1] if len(row) > 1 else None
                if "SPONSOR" in label or "DIRECTOR" in label:
                    result["sponsor_pct"] = to_decimal(value)
                elif "INSTITUT" in label:
                    result["institution_pct"] = to_decimal(value)
                elif "PUBLIC" in label or "GENERAL" in label:
                    result["public_pct"] = to_decimal(value)
                elif "FOREIGN" in label:
                    result["foreign_pct"] = to_decimal(value)

        return pd.DataFrame([result])

    async def fetch(self, ticker: str, **kwargs: Any) -> AdapterResult:
        try:
            import bdshare as bd
        except ImportError as exc:
            raise AdapterError(self.name, "bdshare not installed", retryable=False) from exc

        ticker = normalize_ticker(ticker)
        try:
            raw: list[pd.DataFrame] = bd.get_company_info(ticker)
        except Exception as exc:
            raise AdapterError(self.name, f"get_company_info({ticker}) failed: {exc}") from exc

        if not raw:
            raise AdapterError(self.name, f"no data for {ticker}", retryable=True)

        normalized = self.normalize(raw, ticker=ticker)
        return AdapterResult(
            data=normalized,
            source_name=self.name,
            fetched_at=datetime.now(timezone.utc),
            quality="partial",  # bdshare fundamentals are incomplete vs AmarStock
            records=len(normalized),
            raw_sample={f"df_{i}": (df.iloc[0].to_dict() if len(df) > 0 else {}) for i, df in enumerate(raw)},
        )

    async def health_check(self) -> bool:
        try:
            import bdshare as bd
            raw = bd.get_company_info("SQURPHARMA")
            return raw is not None and len(raw) > 0
        except Exception:
            return False
