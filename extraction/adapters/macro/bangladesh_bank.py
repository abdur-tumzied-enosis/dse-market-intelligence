"""Bangladesh Bank HTML scrapers for macro indicators."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
import pandas as pd
from bs4 import BeautifulSoup, Tag

from extraction.base import AdapterError, AdapterResult, BaseAdapter

_BB_BASE = "https://www.bb.org.bd"

# WARNING: bb.org.bd deploys F5 BIG-IP CAPTCHA on all pages.
# This adapter is best-effort — it will be blocked in most automated contexts.
# WorldBankAdapter is the reliable fallback and should be priority 1 in the registry.
# BB URLs confirmed from sitemap 2026-05-21: /en/index.php/... pattern only.
# Old .php-style URLs (econdata/inflation.php etc.) are dead — 404 or redirect.

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.bb.org.bd/en/index.php",
}

# Per-indicator scrape config — URLs verified from bb.org.bd sitemap 2026-05-21
_CONFIG: dict[str, dict[str, Any]] = {
    "policy_rate": {
        "url": f"{_BB_BASE}/en/index.php/monetaryactivity/index",
        "unit": "percent",
        "period_type": "monthly",
        "value_keywords": ["repo", "rate", "policy"],
        "date_keywords": ["date", "effective", "month", "period"],
    },
    "cpi": {
        "url": f"{_BB_BASE}/en/index.php/econdata/inflation",
        "unit": "percent",
        "period_type": "monthly",
        "value_keywords": ["general", "cpi", "point", "inflation", "overall"],
        "date_keywords": ["month", "period", "date", "year"],
    },
    "usd_bdt": {
        "url": f"{_BB_BASE}/en/index.php/econdata/exchangerate",
        "unit": "bdt_per_usd",
        "period_type": "daily",
        "value_keywords": ["usd", "selling", "taka", "rate", "dollar"],
        "date_keywords": ["date", "period"],
    },
    "remittance": {
        # Sitemap label: "Wage earner's remittance inflow" — note URL spelling "wageremitance"
        "url": f"{_BB_BASE}/en/index.php/econdata/wageremitance",
        "unit": "usd_million",
        "period_type": "monthly",
        "value_keywords": ["total", "amount", "remittance", "inward", "wage"],
        "date_keywords": ["month", "period", "date", "year"],
    },
    "gdp": {
        # Sitemap label: "National income aggregates" — includes GDP/GNI data
        "url": f"{_BB_BASE}/en/index.php/econdata/nationalincome",
        "unit": "percent",
        "period_type": "annual",
        "value_keywords": ["gdp", "growth", "real", "percent", "rate"],
        "date_keywords": ["year", "period", "fy", "fiscal"],
    },
}

_MONTH_MAP: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _parse_period(text: str) -> str | None:
    """Parse a date/period cell → 'YYYY-MM' or 'YYYY' string. Returns None if unparseable."""
    t = text.strip()
    if not t or t in ("-", "N/A", "n/a", "NA"):
        return None

    # ISO: 2024-07 or 2024-07-15
    m = re.match(r"(\d{4})-(\d{2})(?:-\d{2})?", t)
    if m:
        return f"{m.group(1)}-{m.group(2)}"

    # DD/MM/YYYY or DD-MM-YYYY
    m = re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", t)
    if m:
        return f"{m.group(3)}-{m.group(2).zfill(2)}"

    # "July 2024" or "Jul-24" or "Jul 2024"
    m = re.match(r"([A-Za-z]+)[,\-\s]+(\d{2,4})", t)
    if m:
        month_name = m.group(1).lower()
        year_str = m.group(2)
        month_num = _MONTH_MAP.get(month_name)
        if month_num:
            year = int(year_str)
            if year < 100:
                year += 2000
            return f"{year}-{month_num:02d}"

    # "2024-25" or "FY2024-25" fiscal year → use start year
    m = re.match(r"(?:fy)?(\d{4})-\d{2,4}", t, re.IGNORECASE)
    if m:
        return m.group(1)

    # Bare year: 2024
    m = re.match(r"^(\d{4})$", t)
    if m:
        return m.group(1)

    return None


def _to_decimal(text: str) -> Decimal | None:
    cleaned = re.sub(r"[^\d.\-]", "", text.strip())
    if not cleaned or cleaned in ("-", "."):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _score_table(table: Tag) -> int:
    """Higher score = more likely to be the main data table."""
    rows = table.find_all("tr")
    if len(rows) < 3:
        return 0
    numeric_cells = 0
    for row in rows[1:8]:
        for cell in row.find_all(["td", "th"]):
            if _to_decimal(cell.get_text()) is not None:
                numeric_cells += 1
    return len(rows) * 3 + numeric_cells


def _best_table(soup: BeautifulSoup) -> Tag | None:
    best: Tag | None = None
    best_score = 0
    for table in soup.find_all("table"):
        score = _score_table(table)
        if score > best_score:
            best_score = score
            best = table
    return best


def _find_col_idx(headers: list[str], keywords: list[str]) -> int:
    """Return first header index matching any keyword. -1 if not found."""
    for i, h in enumerate(headers):
        h_lower = h.lower()
        if any(k in h_lower for k in keywords):
            return i
    return -1


def _infer_value_col(rows: list[Tag], skip_idx: int) -> int:
    """Scan first data rows to find first non-skip column with numeric value."""
    for row in rows[:5]:
        cells = row.find_all(["td", "th"])
        for i, cell in enumerate(cells):
            if i == skip_idx:
                continue
            if _to_decimal(cell.get_text()) is not None:
                return i
    return 1  # last resort


def _parse_table(table: Tag, indicator: str, cfg: dict[str, Any]) -> pd.DataFrame:
    rows = table.find_all("tr")
    if len(rows) < 2:
        return pd.DataFrame()

    # Header row
    headers = [c.get_text(strip=True).upper() for c in rows[0].find_all(["th", "td"])]

    date_idx = _find_col_idx(headers, cfg["date_keywords"])
    if date_idx == -1:
        date_idx = 0

    val_idx = _find_col_idx(headers, cfg["value_keywords"])
    if val_idx == -1 or val_idx == date_idx:
        val_idx = _infer_value_col(rows[1:], date_idx)

    records = []
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) <= max(date_idx, val_idx):
            continue

        period = _parse_period(cells[date_idx].get_text(strip=True))
        if not period:
            continue

        value = _to_decimal(cells[val_idx].get_text(strip=True))
        if value is None:
            continue

        records.append({
            "indicator": indicator,
            "value": value,
            "unit": cfg["unit"],
            "period": period,
            "period_type": cfg["period_type"],
        })

    return pd.DataFrame(records) if records else pd.DataFrame()


class BangladeshBankAdapter(BaseAdapter):
    """
    Bangladesh Bank HTML scraper — parameterized by indicator name.

    Supported indicators: policy_rate, cpi, usd_bdt, remittance
    Parses the main data table from the relevant bb.org.bd HTML page.
    """

    timeout_seconds = 30

    def __init__(self, indicator: str, priority: int = 1) -> None:
        if indicator not in _CONFIG:
            raise ValueError(f"Unknown BB indicator: {indicator!r}. Valid: {sorted(_CONFIG)}")
        self.indicator = indicator
        self.priority = priority
        self.name = f"bb_{indicator}"
        self._cfg = _CONFIG[indicator]

    async def fetch(self, **kwargs: Any) -> AdapterResult:
        url = self._cfg["url"]
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                headers=_HEADERS,
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AdapterError(self.name, f"HTTP {exc.response.status_code}", retryable=True) from exc
        except Exception as exc:
            raise AdapterError(self.name, f"request failed: {exc}", retryable=True) from exc

        try:
            df = self.normalize(resp.text)
        except Exception as exc:
            raise AdapterError(self.name, f"parse failed: {exc}", retryable=False) from exc

        if df.empty:
            raise AdapterError(self.name, "no records extracted from HTML table", retryable=False)

        return AdapterResult(
            data=df,
            source_name=self.name,
            fetched_at=datetime.now(timezone.utc),
            quality="ok",
            records=len(df),
            raw_sample=df.iloc[0].to_dict(),
        )

    def normalize(self, raw: Any) -> pd.DataFrame:
        if isinstance(raw, str):
            soup = BeautifulSoup(raw, "lxml")
            table = _best_table(soup)
            if table is None:
                raise ValueError("no suitable data table found in page HTML")
            df = _parse_table(table, self.indicator, self._cfg)
        elif isinstance(raw, pd.DataFrame):
            df = raw.copy()
        else:
            raise TypeError(f"unexpected raw type: {type(raw)}")

        if df.empty:
            raise ValueError("table parse returned no records")

        df["source"] = self.name
        df["fetched_at"] = datetime.now(timezone.utc)
        # Most recent first
        return df.iloc[::-1].reset_index(drop=True)

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(
                timeout=10, headers=_HEADERS, follow_redirects=True
            ) as c:
                resp = await c.get(self._cfg["url"])
                return resp.status_code == 200 and len(resp.content) > 1000
        except Exception:
            return False
