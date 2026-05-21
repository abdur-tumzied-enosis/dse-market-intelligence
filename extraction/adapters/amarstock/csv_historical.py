from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import normalize_ticker, to_decimal

# Static hash embedded in AmarStock SPA — no auth required.
QUOTES_BASE = "https://www.amarstock.com/qoutes/3ace8d562de8"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DSEIntelBot/1.0)"}

_MS_RE = re.compile(r"(\d{10,13})")


def _ms_to_utc(val: Any) -> datetime | None:
    """Parse /Date(1778544000000)/ or bare int ms to UTC datetime."""
    m = _MS_RE.search(str(val))
    if not m:
        return None
    ts = int(m.group(1))
    if ts > 1e12:
        ts /= 1000
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (ValueError, OSError):
        return None


class AmarStockCSVAdapter(BaseAdapter):
    """
    AmarStock historical quotes — per-ticker OHLV (no Open/Close).

    Endpoint: GET https://www.amarstock.com/qoutes/3ace8d562de8/{ticker}
    Returns JSON array (~688 records, approx 2018-present, no pagination).
    No auth required.

    NOTE: Response fields are MaxPrice (high) + MinPrice (low) only.
    Open and Close are NOT provided. Use BDShareHistoricalAdapter for
    full OHLCV. This adapter is useful as a volume/range cross-check
    or fallback when bdshare is unavailable.

    Fields: Date, Scrip, MaxPrice (high), MinPrice (low), Trades, Volume, Value
    """
    name = "amarstock_historical"
    priority = 2
    timeout_seconds = 20

    def __init__(
        self,
        base_url: str = QUOTES_BASE,
        request_delay: float = 1.0,
    ) -> None:
        self._base_url = base_url
        self._delay = request_delay

    def normalize(self, raw: list[dict[str, Any]], ticker: str = "") -> pd.DataFrame:
        rows = []
        for item in raw:
            rows.append({
                "ticker":    normalize_ticker(ticker or str(item.get("Scrip", ""))),
                "date":      _ms_to_utc(item.get("Date")),
                "high":      to_decimal(item.get("MaxPrice")),
                "low":       to_decimal(item.get("MinPrice")),
                "volume":    item.get("Volume"),
                "trades":    item.get("Trades"),
                "value_bdt": to_decimal(item.get("Value")),
                "source":    self.name,
            })
        df = pd.DataFrame(rows)
        if "date" in df.columns:
            df = df.dropna(subset=["date"]).sort_values("date")
        return df

    async def fetch(self, ticker: str, **kwargs: Any) -> AdapterResult:
        ticker = normalize_ticker(ticker)
        url = f"{self._base_url}/{ticker}"

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=True,
                headers=HEADERS,
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                raw: list[dict[str, Any]] = resp.json()
        except httpx.HTTPStatusError as exc:
            raise AdapterError(self.name, f"HTTP {exc.response.status_code} for {ticker}", retryable=True) from exc
        except Exception as exc:
            raise AdapterError(self.name, f"request failed for {ticker}: {exc}", retryable=True) from exc

        if not raw:
            raise AdapterError(self.name, f"empty response for {ticker}", retryable=False)

        normalized = self.normalize(raw, ticker=ticker)
        await asyncio.sleep(self._delay)

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
                resp = await client.get(f"{self._base_url}/GP")
                return resp.status_code == 200 and len(resp.content) > 100
        except Exception:
            return False
