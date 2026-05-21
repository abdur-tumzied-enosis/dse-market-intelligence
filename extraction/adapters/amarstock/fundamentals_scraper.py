from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import normalize_ticker, to_decimal

AMARSTOCK_SCRAPE_BASE = "https://amarstock.com"

_PERCENT_RE = re.compile(r"([\d.]+)\s*%")
_NUMBER_RE = re.compile(r"[\d,]+\.?\d*")


def _extract_number(text: str) -> str | None:
    m = _NUMBER_RE.search(str(text).replace(",", ""))
    return m.group() if m else None


class AmarStockFundamentalsAdapter(BaseAdapter):
    """
    PRIMARY source for fundamentals. Scrapes amarstock.com/stock-chart/{ticker}.

    Returns: EPS, PE, NAV, revenue, net_profit, market_cap,
             shareholding breakdown (sponsor/institution/public/foreign),
             dividend history, fiscal year.

    No JSON API — HTML scrape required. BeautifulSoup4 + lxml.

    Rate limit: 2s between requests (configurable via request_delay).
    Playwright NOT needed — page is server-rendered HTML.
    """
    name = "amarstock_fundamentals"
    priority = 1
    timeout_seconds = 25

    def __init__(
        self,
        base_url: str = AMARSTOCK_SCRAPE_BASE,
        request_delay: float = 2.0,
    ) -> None:
        self._base_url = base_url
        self._delay = request_delay

    def normalize(self, raw: dict[str, Any], ticker: str = "") -> pd.DataFrame:
        result: dict[str, Any] = {
            "ticker":     normalize_ticker(ticker),
            "fetched_at": datetime.now(timezone.utc),
            "source":     self.name,
        }
        result.update(raw)
        return pd.DataFrame([result])

    def _parse_html(self, html: str, ticker: str) -> dict[str, Any]:
        """Extract fundamentals from amarstock.com/stock-chart/{ticker} HTML."""
        try:
            from bs4 import BeautifulSoup
        except ImportError as exc:
            raise ImportError("beautifulsoup4 not installed") from exc

        soup = BeautifulSoup(html, "lxml")
        data: dict[str, Any] = {}

        # Strategy: find key-value pairs in tables across the page.
        # AmarStock uses multiple tables — scan all for known field names.
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all(["td", "th"])
                if len(cells) < 2:
                    continue
                key = cells[0].get_text(strip=True).upper()
                val = cells[1].get_text(strip=True)

                if "EPS" in key and "DILUT" not in key:
                    data["eps"] = to_decimal(_extract_number(val))
                elif "DILUT" in key and "EPS" in key:
                    data["eps_diluted"] = to_decimal(_extract_number(val))
                elif key in ("P/E", "PE RATIO", "P/E RATIO"):
                    data["pe"] = to_decimal(_extract_number(val))
                elif "NAV" in key and "ADJUST" not in key:
                    data["nav"] = to_decimal(_extract_number(val))
                elif "ADJUST" in key and "NAV" in key:
                    data["nav_adjusted"] = to_decimal(_extract_number(val))
                elif "REVENUE" in key or "TURNOVER" in key:
                    data["revenue_bdt"] = to_decimal(_extract_number(val))
                elif "NET PROFIT" in key or "NET INCOME" in key:
                    data["net_profit_bdt"] = to_decimal(_extract_number(val))
                elif "MARKET CAP" in key:
                    data["market_cap_bdt"] = to_decimal(_extract_number(val))
                elif "FISCAL YEAR" in key or "FY" == key:
                    fy_match = re.search(r"(20\d\d)", val)
                    if fy_match:
                        data["fiscal_year"] = int(fy_match.group(1))
                # Shareholding
                elif "SPONSOR" in key or "DIRECTOR" in key:
                    m = _PERCENT_RE.search(val)
                    if m:
                        data["sponsor_pct"] = to_decimal(m.group(1))
                elif "INSTITUT" in key:
                    m = _PERCENT_RE.search(val)
                    if m:
                        data["institution_pct"] = to_decimal(m.group(1))
                elif "PUBLIC" in key or "GENERAL" in key:
                    m = _PERCENT_RE.search(val)
                    if m:
                        data["public_pct"] = to_decimal(m.group(1))
                elif "FOREIGN" in key:
                    m = _PERCENT_RE.search(val)
                    if m:
                        data["foreign_pct"] = to_decimal(m.group(1))

        return data

    async def fetch(self, ticker: str, **kwargs: Any) -> AdapterResult:
        ticker = normalize_ticker(ticker)
        url = f"{self._base_url}/stock-chart/{ticker}"

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; DSEIntelBot/1.0)"},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text
        except httpx.HTTPStatusError as exc:
            raise AdapterError(self.name, f"HTTP {exc.response.status_code} for {ticker}", retryable=True) from exc
        except Exception as exc:
            raise AdapterError(self.name, f"request failed for {ticker}: {exc}", retryable=True) from exc

        if not html or len(html) < 1000:
            raise AdapterError(self.name, f"suspiciously short response for {ticker}: {len(html)} chars", retryable=True)

        parsed = self._parse_html(html, ticker)
        if not parsed:
            raise AdapterError(self.name, f"no fundamental fields parsed from HTML for {ticker}", retryable=False)

        normalized = self.normalize(parsed, ticker=ticker)

        await asyncio.sleep(self._delay)  # rate limit

        return AdapterResult(
            data=normalized,
            source_name=self.name,
            fetched_at=datetime.now(timezone.utc),
            quality="ok" if len(parsed) >= 3 else "partial",
            records=len(normalized),
            raw_sample=parsed,
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{self._base_url}/stock-chart/GP")
                return resp.status_code == 200 and len(resp.text) > 1000
        except Exception:
            return False
