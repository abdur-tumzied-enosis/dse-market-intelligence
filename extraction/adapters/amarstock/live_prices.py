from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import normalize_ticker, to_decimal

AMARSTOCK_API_URL = "https://api.amarstock.com/latest-share-price"


class AmarStockLivePricesAdapter(BaseAdapter):
    """
    AmarStock unofficial REST API — live share prices.
    Used as fallback when bdshare is unavailable.

    Endpoint: GET https://api.amarstock.com/latest-share-price
    Returns JSON array of objects.
    """
    name = "amarstock_live_prices"
    priority = 2
    timeout_seconds = 20

    def __init__(self, base_url: str = AMARSTOCK_API_URL) -> None:
        self._url = base_url

    def normalize(self, raw: list[dict[str, Any]]) -> pd.DataFrame:
        """
        AmarStock JSON fields (confirmed from smoke test — update after running):
        tradingCode, lastTradedPrice, openPrice, highPrice, lowPrice,
        closingPrice, yesterdayClosingPrice, change, percentChange,
        volume, value, trade
        """
        rows = []
        fetched = datetime.now(timezone.utc)
        for item in raw:
            rows.append({
                "ticker":     normalize_ticker(str(item.get("tradingCode", ""))),
                "open":       to_decimal(item.get("openPrice")),
                "high":       to_decimal(item.get("highPrice")),
                "low":        to_decimal(item.get("lowPrice")),
                "close":      to_decimal(item.get("lastTradedPrice") or item.get("closingPrice")),
                "prev_close": to_decimal(item.get("yesterdayClosingPrice")),
                "change_pct": to_decimal(item.get("percentChange")),
                "volume":     item.get("volume"),
                "trades":     item.get("trade"),
                "value_bdt":  to_decimal(item.get("value")),
                "fetched_at": fetched,
                "source":     self.name,
            })
        return pd.DataFrame(rows)

    async def fetch(self, **kwargs: Any) -> AdapterResult:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
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
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(self._url)
                return resp.status_code == 200
        except Exception:
            return False
