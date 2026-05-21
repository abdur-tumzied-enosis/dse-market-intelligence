"""World Bank Open Data API adapter — fallback for BD macro indicators."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
import pandas as pd

from extraction.base import AdapterError, AdapterResult, BaseAdapter

# mrv=12 = most recent 12 values; per_page must be >= mrv
_WB_URL = (
    "https://api.worldbank.org/v2/country/BD/indicator/{code}"
    "?format=json&mrv=12&per_page=12"
)

# indicator_code → (canonical_name, unit)
_META: dict[str, tuple[str, str]] = {
    "FP.CPI.TOTL.ZG":    ("cpi",          "percent"),
    "NY.GDP.MKTP.KD.ZG": ("gdp",          "percent"),
    "NY.GDP.MKTP.CD":    ("gdp",          "usd_current"),
    "BX.TRF.PWKR.CD.DT": ("remittance",   "usd_current"),
    "PA.NUS.FCRF":       ("usd_bdt",      "bdt_per_usd"),
    "FR.INR.RINR":       ("policy_rate",  "percent"),
}


def _parse_wb_period(date_str: str) -> tuple[str, str]:
    """World Bank date string → (period_str, period_type)."""
    s = date_str.strip()
    if re.match(r"^\d{4}$", s):
        return s, "annual"
    m = re.match(r"^(\d{4})Q(\d)$", s)
    if m:
        return f"{m.group(1)}-Q{m.group(2)}", "quarterly"
    m = re.match(r"^(\d{4})M(\d{2})$", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}", "monthly"
    return s, "unknown"


class WorldBankAdapter(BaseAdapter):
    """
    World Bank Open Data API — fallback for Bangladesh macro indicators.

    Data lags ~3-6 months for annual figures. Use only when primary BB source fails.
    Parameterized by WB indicator code (e.g. 'FP.CPI.TOTL.ZG').
    """

    timeout_seconds = 30

    def __init__(self, indicator: str, priority: int = 2, unit: str | None = None) -> None:
        self.indicator_code = indicator
        self.priority = priority
        meta = _META.get(indicator, (indicator, unit or "unknown"))
        self._indicator_name: str = meta[0]
        self._unit: str = unit if unit is not None else meta[1]
        self.name = f"worldbank_{indicator.replace('.', '_').lower()}"

    async def fetch(self, **kwargs: Any) -> AdapterResult:
        url = _WB_URL.format(code=self.indicator_code)
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AdapterError(self.name, f"HTTP {exc.response.status_code}", retryable=True) from exc
        except Exception as exc:
            raise AdapterError(self.name, f"request failed: {exc}", retryable=True) from exc

        try:
            df = self.normalize(resp.json())
        except Exception as exc:
            raise AdapterError(self.name, f"parse failed: {exc}", retryable=False) from exc

        if df.empty:
            raise AdapterError(
                self.name,
                f"World Bank returned no data for indicator {self.indicator_code}",
                retryable=False,
            )

        return AdapterResult(
            data=df,
            source_name=self.name,
            fetched_at=datetime.now(timezone.utc),
            quality="ok",
            records=len(df),
            raw_sample=df.iloc[0].to_dict(),
        )

    def normalize(self, raw: Any) -> pd.DataFrame:
        # WB response: [metadata_dict, [{date, value, ...}, ...]]
        if not isinstance(raw, list) or len(raw) < 2:
            raise ValueError(f"unexpected WB response structure: {type(raw)}")

        entries = raw[1]
        if not entries:
            raise ValueError("World Bank returned empty data array")

        now = datetime.now(timezone.utc)
        records = []
        for entry in entries:
            if entry.get("value") is None:
                continue
            period, period_type = _parse_wb_period(entry.get("date", ""))
            records.append({
                "indicator": self._indicator_name,
                "value": Decimal(str(entry["value"])),
                "unit": self._unit,
                "period": period,
                "period_type": period_type,
                "source": self.name,
                "fetched_at": now,
            })

        return pd.DataFrame(records) if records else pd.DataFrame()

    async def health_check(self) -> bool:
        try:
            url = _WB_URL.format(code=self.indicator_code)
            async with httpx.AsyncClient(timeout=10) as c:
                resp = await c.get(url)
                return resp.status_code == 200
        except Exception:
            return False
