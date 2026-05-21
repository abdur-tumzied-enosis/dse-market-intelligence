from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import normalize_ticker, to_decimal

# Static hash embedded in AmarStock SPA — does not require session/auth.
STOCK_DETAIL_BASE = "https://www.amarstock.com/data/1981d726120d"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DSEIntelBot/1.0)"}


def _ms_to_dt(ms: Any) -> datetime | None:
    """Convert /Date(1779521900000)/ MS timestamp to UTC datetime."""
    if ms is None:
        return None
    s = str(ms)
    import re
    m = re.search(r"(\d{10,13})", s)
    if not m:
        return None
    ts = int(m.group(1))
    if ts > 1e12:
        ts /= 1000
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (ValueError, OSError):
        return None


class AmarStockFundamentalsAdapter(BaseAdapter):
    """
    PRIMARY source for per-ticker fundamentals.

    Endpoint: GET https://www.amarstock.com/data/1981d726120d/{ticker}
    Returns JSON — no auth required.

    Response fields (confirmed 2026-05-21):
        EPS, AuditedPE, UnAuditedPE, Q1Eps-Q4Eps
        NAV, NavPrice, DividentYield, freefloat
        MarketCap, PaidUpCap, AuthorizedCap, TotalSecurities, ReserveSurplus
        SponsorDirector, Govt, Institute, Foreign, Public  (latest period)
        ShareHoldingPercentage  — date string of latest shareholding snapshot
        SponsorDirector1/2, Govt1/2, etc.  — prior periods
        ListingYear, LastAGMHeld, YE (fiscal year end month)
        Address, Contact, Email, Web
        ma10/20/50/100/200, ema10/20/50/100/200, stockBeta
        news1stdate-news5stdate, news1sttitle-news5sttitle, news1stbody-news5stbody
    """
    name = "amarstock_fundamentals"
    priority = 1
    timeout_seconds = 20

    def __init__(
        self,
        base_url: str = STOCK_DETAIL_BASE,
        request_delay: float = 1.0,
    ) -> None:
        self._base_url = base_url
        self._delay = request_delay

    def normalize(self, raw: dict[str, Any], ticker: str = "") -> pd.DataFrame:
        t = normalize_ticker(ticker or str(raw.get("Scrip", "")))
        fetched = datetime.now(timezone.utc)

        row: dict[str, Any] = {
            "ticker":           t,
            "full_name":        raw.get("FullName"),
            "listing_year":     raw.get("ListingYear"),
            "market_category":  raw.get("MarketCategory"),
            "fiscal_year_end":  raw.get("YE"),
            "last_agm":         raw.get("LastAGMHeld"),
            # Fundamentals
            "eps":              to_decimal(raw.get("EPS")),
            "eps_q1":           to_decimal(raw.get("Q1Eps")),
            "eps_q2":           to_decimal(raw.get("Q2Eps")),
            "eps_q3":           to_decimal(raw.get("Q3Eps")),
            "eps_q4":           to_decimal(raw.get("Q4Eps")),
            "pe_audited":       to_decimal(raw.get("AuditedPE")),
            "pe_unaudited":     to_decimal(raw.get("UnAuditedPE")),
            "nav":              to_decimal(raw.get("NAV")),
            "nav_price_ratio":  to_decimal(raw.get("NavPrice")),
            "dividend_yield":   to_decimal(raw.get("DividentYield")),
            "free_float":       to_decimal(raw.get("freefloat")),
            # Balance sheet
            "market_cap_mn":    to_decimal(raw.get("MarketCap")),
            "paid_up_cap_mn":   to_decimal(raw.get("PaidUpCap")),
            "authorized_cap_mn": to_decimal(raw.get("AuthorizedCap")),
            "total_securities": raw.get("TotalSecurities"),
            "reserve_surplus_mn": to_decimal(raw.get("ReserveSurplus")),
            "short_loan_mn":    to_decimal(raw.get("ShortLoan")),
            "long_loan_mn":     to_decimal(raw.get("LongLoan")),
            # Shareholding — latest period
            "shareholding_date":  raw.get("ShareHoldingPercentage"),
            "sponsor_pct":      to_decimal(raw.get("SponsorDirector")),
            "govt_pct":         to_decimal(raw.get("Govt")),
            "institution_pct":  to_decimal(raw.get("Institute")),
            "foreign_pct":      to_decimal(raw.get("Foreign")),
            "public_pct":       to_decimal(raw.get("Public")),
            # Shareholding — prior periods
            "shareholding_date_1": raw.get("ShareHoldingPercentage1"),
            "sponsor_pct_1":    to_decimal(raw.get("SponsorDirector1")),
            "institution_pct_1": to_decimal(raw.get("Institute1")),
            "foreign_pct_1":    to_decimal(raw.get("Foreign1")),
            "public_pct_1":     to_decimal(raw.get("Public1")),
            "shareholding_date_2": raw.get("ShareHoldingPercentage2"),
            "sponsor_pct_2":    to_decimal(raw.get("SponsorDirector2")),
            "institution_pct_2": to_decimal(raw.get("Institute2")),
            "foreign_pct_2":    to_decimal(raw.get("Foreign2")),
            "public_pct_2":     to_decimal(raw.get("Public2")),
            # Technical signals
            "ma10":   raw.get("ma10"),
            "ma20":   raw.get("ma20"),
            "ma50":   raw.get("ma50"),
            "ma100":  raw.get("ma100"),
            "ma200":  raw.get("ma200"),
            "ema10":  raw.get("ema10"),
            "ema50":  raw.get("ema50"),
            "beta":   to_decimal(raw.get("stockBeta")),
            # Company info
            "address": raw.get("Address"),
            "web":     raw.get("Web"),
            "email":   raw.get("Email"),
            "rating":  raw.get("Rating"),
            # Recent news
            "news1_date":  _ms_to_dt(raw.get("news1stdate")),
            "news1_title": raw.get("news1sttitle"),
            "news2_date":  _ms_to_dt(raw.get("news2stdate")),
            "news2_title": raw.get("news2sttitle"),
            "news3_date":  _ms_to_dt(raw.get("news3stdate")),
            "news3_title": raw.get("news3sttitle"),
            "fetched_at": fetched,
            "source":     self.name,
        }
        return pd.DataFrame([row])

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
                raw: dict[str, Any] = resp.json()
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
            quality="ok" if raw.get("EPS") is not None else "partial",
            records=len(normalized),
            raw_sample=raw,
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10, headers=HEADERS) as client:
                resp = await client.get(f"{self._base_url}/GP")
                return resp.status_code == 200 and b"Scrip" in resp.content
        except Exception:
            return False
