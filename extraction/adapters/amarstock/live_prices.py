from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import normalize_ticker, to_decimal

# Static hash embedded in AmarStock SPA — does not require session/auth.
LATEST_PRICE_URL = "https://www.amarstock.com/LatestPrice/dbfd2587c77f"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DSEIntelBot/1.0)"}


class AmarStockLivePricesAdapter(BaseAdapter):
    """
    AmarStock SPA internal API — all-stock live prices + fundamentals.

    Endpoint: GET https://www.amarstock.com/LatestPrice/dbfd2587c77f
    Returns JSON array (~430 items, one per DSE-listed instrument).

    Response fields (confirmed 2026-05-21):
        Scrip           ticker
        LTP             last traded price (= Close during session)
        Open, High, Low, Close, YCP
        Change, ChangePer   price change + pct
        Volume, Trade, Value
        PE, UnAuditedPE, AuditedPE
        Eps, Q1Eps-Q4Eps
        NAV, NavPrice, FreeFloat, MarketCap
        SponsorDirector, Govt, Institute, Foreign, Public  (shareholding %)
        InstrumentType, BusinessSegment, FullName, MarketCategory
        YearEnd1-8, YearEndPECont1-8  (historic PE by fiscal year)
    """
    name = "amarstock_live_prices"
    priority = 2
    timeout_seconds = 20

    def __init__(self, url: str = LATEST_PRICE_URL) -> None:
        self._url = url

    def normalize(self, raw: list[dict[str, Any]]) -> pd.DataFrame:
        rows = []
        fetched = datetime.now(timezone.utc)
        for item in raw:
            rows.append({
                "ticker":        normalize_ticker(str(item.get("Scrip", ""))),
                "open":          to_decimal(item.get("Open")),
                "high":          to_decimal(item.get("High")),
                "low":           to_decimal(item.get("Low")),
                "close":         to_decimal(item.get("Close")),
                "ltp":           to_decimal(item.get("LTP")),
                "prev_close":    to_decimal(item.get("YCP")),
                "change":        to_decimal(item.get("Change")),
                "change_pct":    to_decimal(item.get("ChangePer")),
                "volume":        item.get("Volume"),
                "trades":        item.get("Trade"),
                "value_mn":      to_decimal(item.get("Value")),
                "market_cap_mn": to_decimal(item.get("MarketCap")),
                "pe":            to_decimal(item.get("PE")),
                "pe_audited":    to_decimal(item.get("AuditedPE")),
                "pe_unaudited":  to_decimal(item.get("UnAuditedPE")),
                "eps":           to_decimal(item.get("Eps")),
                "nav":           to_decimal(item.get("NAV")),
                "free_float":    to_decimal(item.get("FreeFloat")),
                "sponsor_pct":   to_decimal(item.get("SponsorDirector")),
                "govt_pct":      to_decimal(item.get("Govt")),
                "institution_pct": to_decimal(item.get("Institute")),
                "foreign_pct":   to_decimal(item.get("Foreign")),
                "public_pct":    to_decimal(item.get("Public")),
                "instrument_type": item.get("InstrumentType"),
                "sector":        item.get("BusinessSegment"),
                "full_name":     item.get("FullName"),
                "market_cat":    item.get("MarketCategory"),
                "fetched_at":    fetched,
                "source":        self.name,
            })
        return pd.DataFrame(rows)

    async def fetch(self, **kwargs: Any) -> AdapterResult:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                headers=HEADERS,
                follow_redirects=True,
            ) as client:
                resp = await client.get(self._url)
                resp.raise_for_status()
                raw: list[dict[str, Any]] = resp.json()
        except httpx.HTTPStatusError as exc:
            raise AdapterError(self.name, f"HTTP {exc.response.status_code}", retryable=True) from exc
        except Exception as exc:
            raise AdapterError(self.name, f"request failed: {exc}", retryable=True) from exc

        if not raw:
            raise AdapterError(self.name, "returned empty list", retryable=True)

        normalized = self.normalize(raw)
        return AdapterResult(
            data=normalized,
            source_name=self.name,
            fetched_at=datetime.now(timezone.utc),
            quality="ok",
            records=len(normalized),
            raw_sample=raw[0] if raw else {},
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10, headers=HEADERS) as client:
                resp = await client.get(self._url)
                return resp.status_code == 200 and len(resp.content) > 1000
        except Exception:
            return False
