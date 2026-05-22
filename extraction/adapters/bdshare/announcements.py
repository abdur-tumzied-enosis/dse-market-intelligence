from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import bd_date_str_to_utc, normalize_ticker, to_decimal


class BDShareAnnouncementsAdapter(BaseAdapter):
    """bdshare get_corporate_announcements() — general corporate announcements."""
    name = "bdshare_announcements"
    priority = 1
    timeout_seconds = 20

    def normalize(self, raw: pd.DataFrame) -> pd.DataFrame:
        df = raw.copy()
        df.columns = [c.strip().upper().replace(" ", "_") for c in df.columns]

        out = pd.DataFrame()
        ticker_col = next((c for c in df.columns if "CODE" in c or "TICKER" in c), df.columns[0])
        out["ticker"]   = df[ticker_col].apply(normalize_ticker)

        date_col = next((c for c in df.columns if "DATE" in c or "TIME" in c), None)
        if date_col:
            out["published_at"] = df[date_col].apply(
                lambda v: bd_date_str_to_utc(str(v)) if v else None
            )
        else:
            out["published_at"] = None

        cat_col = next((c for c in df.columns if "CATEGORY" in c or "TYPE" in c), None)
        out["category"] = df[cat_col].str.strip() if cat_col else ""

        detail_col = next((c for c in df.columns if "DETAIL" in c or "DESC" in c or "MESSAGE" in c), None)
        out["details"] = df[detail_col].str.strip() if detail_col else ""

        out["source"] = self.name
        return out

    async def fetch(self, **kwargs: Any) -> AdapterResult:
        try:
            import bdshare as bd
        except ImportError as exc:
            raise AdapterError(self.name, "bdshare not installed", retryable=False) from exc

        try:
            raw: pd.DataFrame = bd.get_corporate_announcements()
        except Exception as exc:
            raise AdapterError(self.name, f"get_corporate_announcements failed: {exc}") from exc

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
            raw = bd.get_corporate_announcements()
            return raw is not None and len(raw) > 0
        except Exception:
            return False


class BDSharePSNAdapter(BaseAdapter):
    """bdshare get_price_sensitive_news() — price-sensitive announcements."""
    name = "bdshare_psn"
    priority = 1
    timeout_seconds = 20

    def normalize(self, raw: pd.DataFrame) -> pd.DataFrame:
        df = raw.copy()
        df.columns = [c.strip().upper().replace(" ", "_") for c in df.columns]

        out = pd.DataFrame()
        ticker_col = next((c for c in df.columns if "CODE" in c or "TICKER" in c), df.columns[0])
        out["ticker"] = df[ticker_col].apply(normalize_ticker)

        date_col = next((c for c in df.columns if "DATE" in c), None)
        out["published_at"] = df[date_col].apply(
            lambda v: bd_date_str_to_utc(str(v)) if v else None
        ) if date_col else None

        head_col = next((c for c in df.columns if "HEAD" in c or "TITLE" in c or "NEWS" in c), None)
        out["headline"] = df[head_col].str.strip() if head_col else ""

        out["source"] = self.name
        return out

    async def fetch(self, **kwargs: Any) -> AdapterResult:
        try:
            import bdshare as bd
        except ImportError as exc:
            raise AdapterError(self.name, "bdshare not installed", retryable=False) from exc

        try:
            raw: pd.DataFrame = bd.get_price_sensitive_news()
        except Exception as exc:
            raise AdapterError(self.name, f"get_price_sensitive_news failed: {exc}") from exc

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
            raw = bd.get_price_sensitive_news()
            return raw is not None
        except Exception:
            return False


class BDShareAGMAdapter(BaseAdapter):
    """bdshare get_agm_news() — AGM announcements + dividend declarations."""
    name = "bdshare_agm"
    priority = 1
    timeout_seconds = 20

    def normalize(self, raw: pd.DataFrame) -> pd.DataFrame:
        df = raw.copy()
        df.columns = [c.strip().upper().replace(" ", "_") for c in df.columns]

        out = pd.DataFrame()
        ticker_col = next((c for c in df.columns if "CODE" in c or "TICKER" in c), df.columns[0])
        out["ticker"] = df[ticker_col].apply(normalize_ticker)

        agm_col = next((c for c in df.columns if "AGM" in c and "DATE" in c), None)

        def _parse_agm_date(v: object) -> object:
            if not v:
                return None
            # bdshare agmDate contains embedded "\r\n  " whitespace, e.g.
            # "December\r\n  29, 2020" — normalize to "December 29, 2020"
            cleaned = " ".join(str(v).split())
            return bd_date_str_to_utc(cleaned, fmt="%B %d, %Y")

        out["agm_date"] = df[agm_col].apply(_parse_agm_date) if agm_col else None

        cash_col = next((c for c in df.columns if "CASH" in c), None)
        out["cash_div_pct"] = df[cash_col].apply(to_decimal) if cash_col else None

        stock_col = next((c for c in df.columns if "STOCK" in c and "DIV" in c), None)
        out["stock_div_pct"] = df[stock_col].apply(to_decimal) if stock_col else None

        out["source"] = self.name
        return out

    async def fetch(self, **kwargs: Any) -> AdapterResult:
        try:
            import bdshare as bd
        except ImportError as exc:
            raise AdapterError(self.name, "bdshare not installed", retryable=False) from exc

        try:
            raw: pd.DataFrame = bd.get_agm_news()
        except Exception as exc:
            raise AdapterError(self.name, f"get_agm_news failed: {exc}") from exc

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
            raw = bd.get_agm_news()
            return raw is not None
        except Exception:
            return False
