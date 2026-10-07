from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pandas as pd

from extraction.adapters.dse_direct._tls import dse_client
from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import normalize_ticker, to_decimal

# DSE relaunched on www.dse.com.bd (Next.js) in 2026; every dsebd.org/*.php page
# now returns 410 Gone. The new site's own JSON feed (confirmed 2026-10-07):
LIVE_PRICE_URL = "https://www.dse.com.bd/api/live/prices"

# Response: {"cols": [...], "rows": [[...], ...], "session": {"isOpen", "phase", ...}}
# cols (2026-10-07): code, ltp, ycp, open, high, low, close, volume, value,
#                    trades, percent, category, board, sector, assetType
# value is in BDT millions; percent is % change vs ycp.

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.dse.com.bd/markets/latest-share-price",
}


class DSEDirectLivePricesAdapter(BaseAdapter):
    """
    DSE official site — all-stock live prices.

    URL: https://www.dse.com.bd/api/live/prices
    Method: JSON API (column-oriented: cols + rows).
    Full OHLC + % change, unlike the old dsebd.org HTML table.
    """
    name = "dse_direct_live_prices"
    priority = 1
    timeout_seconds = 30

    def __init__(self, url: str = LIVE_PRICE_URL) -> None:
        self._url = url

    @staticmethod
    def _parse_json(payload: dict[str, Any]) -> pd.DataFrame:
        cols, rows = payload.get("cols"), payload.get("rows")
        if not cols or rows is None:
            raise ValueError(f"unexpected payload keys: {list(payload)}")
        return pd.DataFrame(rows, columns=cols)

    def normalize(self, raw: pd.DataFrame) -> pd.DataFrame:
        if "code" not in raw.columns:
            raise ValueError(f"no code column; columns={list(raw.columns)}")
        # GOVDBT = treasury bonds quoted in yield, not price — not our universe.
        if "assetType" in raw.columns:
            raw = raw.loc[raw["assetType"] != "GOVDBT"].reset_index(drop=True)

        def _price(col: str) -> pd.Series:
            # Untraded instruments report 0 for ltp/open/high/low — that's "no
            # trade", not a price, and would trip the negative/zero-price check.
            if col not in raw.columns:
                return pd.Series([None] * len(raw), dtype=object)
            return raw[col].apply(lambda v: to_decimal(v) if v else None)

        out = pd.DataFrame()
        out["ticker"] = raw["code"].astype(str).apply(normalize_ticker)
        out["close_official"] = _price("close")
        # ltp = last traded price = effective close; untraded → official close
        out["close"] = _price("ltp").where(lambda s: s.notna(), out["close_official"])
        out["prev_close"] = _price("ycp")
        for col in ("open", "high", "low"):
            out[col] = _price(col)
        for src, dst in (("value", "value_mn"), ("percent", "change_pct")):
            out[dst] = raw[src].apply(to_decimal) if src in raw.columns else None
        out["change"] = [
            c - p if c is not None and p is not None else None
            for c, p in zip(out["close"], out["prev_close"])
        ]
        for col in ("volume", "trades"):
            out[col] = pd.to_numeric(raw[col], errors="coerce") if col in raw.columns else None
        out["fetched_at"] = datetime.now(UTC)
        out["source"] = self.name
        return out.loc[out["ticker"].str.len() > 0]

    async def fetch(self, **kwargs: Any) -> AdapterResult:
        try:
            async with dse_client(
                timeout=self.timeout_seconds,
                headers=HEADERS,
                follow_redirects=True,
            ) as client:
                resp = await client.get(self._url)
                resp.raise_for_status()
                payload = resp.json()
        except httpx.HTTPStatusError as exc:
            raise AdapterError(self.name, f"HTTP {exc.response.status_code}", retryable=True) from exc
        except Exception as exc:
            raise AdapterError(self.name, f"request failed: {exc}", retryable=True) from exc

        try:
            raw = self._parse_json(payload)
        except Exception as exc:
            raise AdapterError(self.name, f"JSON parse failed: {exc}", retryable=False) from exc

        if raw.empty:
            raise AdapterError(self.name, "empty rows", retryable=True)

        try:
            normalized = self.normalize(raw)
        except Exception as exc:
            raise AdapterError(self.name, f"normalize failed: {exc}", retryable=False) from exc

        return AdapterResult(
            data=normalized,
            source_name=self.name,
            fetched_at=datetime.now(UTC),
            quality="ok",
            records=len(normalized),
            raw_sample=raw.iloc[0].to_dict(),
        )

    async def health_check(self) -> bool:
        try:
            async with dse_client(timeout=10, headers=HEADERS, follow_redirects=True) as c:
                resp = await c.get(self._url)
                return resp.status_code == 200 and "rows" in resp.json()
        except Exception:
            return False
