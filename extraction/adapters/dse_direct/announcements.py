from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import bd_date_str_to_utc, normalize_ticker

# Confirmed 2026-05-21: news_archive.php / price_sensitive_news.php return 404.
# display_news.php = today's news (JS-rendered).
# news_archive_7days.php = last 7 days news (JS-rendered).
# Both require Playwright — content loads via JavaScript after page load.
#
# Table structure (confirmed 2026-05-21): single table, single <tbody>.
# Each announcement = 4 consecutive <tr> rows, each with <th> label + <td> value:
#   <th>Trading Code:</th>  <td>BRACBANK</td>
#   <th>News Title:</th>    <td>AGM Notice ...</td>
#   <th>News:</th>          <td>Full body text...</td>
#   <th>Post Date:</th>     <td>2026-05-21</td>
# Followed by 2 separator rows (no <th>/<td> pair), then next announcement.

NEWS_URL    = "https://www.dsebd.org/display_news.php"
NEWS_7D_URL = "https://www.dsebd.org/news_archive_7days.php"

_LABEL_MAP = {
    "Trading Code": "ticker",
    "News Title":   "headline",
    "News":         "details",
    "Post Date":    "date_str",
}


async def _playwright_fetch_news(url: str, adapter_name: str, timeout_s: int) -> list[dict[str, Any]]:
    """
    Render a DSE news page with Playwright and extract announcement rows.

    Returns list of raw dicts: {ticker, date_str, headline, details, category}
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise AdapterError(adapter_name, "playwright not installed", retryable=False) from exc

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle", timeout=timeout_s * 1000)

            try:
                await page.wait_for_selector("table td", timeout=15_000)
            except Exception:
                pass

            right_body = await page.query_selector("#RightBody")
            content_html = await right_body.inner_html() if right_body else await page.content()
            await browser.close()

    except AdapterError:
        raise
    except Exception as exc:
        raise AdapterError(adapter_name, f"Playwright failed: {exc}", retryable=True) from exc

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(content_html, "lxml")

    records: list[dict[str, Any]] = []
    current: dict[str, str] = {}

    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            th = tr.find("th")
            td = tr.find("td")
            if th and td:
                label = th.get_text(strip=True).rstrip(":")
                field = _LABEL_MAP.get(label)
                if field:
                    current[field] = td.get_text(strip=True)
            if len(current) == 4:
                records.append({
                    "ticker":   normalize_ticker(current.get("ticker", "")),
                    "date_str": current.get("date_str", ""),
                    "headline": current.get("headline", ""),
                    "details":  current.get("details", ""),
                    "category": "",
                })
                current = {}

    return records


def _normalize_rows(rows: list[dict[str, Any]], source_name: str) -> pd.DataFrame:
    out_rows = []
    for r in rows:
        out_rows.append({
            "ticker":       normalize_ticker(r.get("ticker", "")),
            "published_at": bd_date_str_to_utc(r["date_str"]) if r.get("date_str") else None,
            "category":     r.get("category", ""),
            "headline":     r.get("headline", ""),
            "details":      r.get("details", ""),
            "source":       source_name,
        })
    return pd.DataFrame(out_rows)


class DSEDirectAnnouncementsAdapter(BaseAdapter):
    """
    DSE official site — today's corporate announcements via Playwright.

    URL: https://www.dsebd.org/display_news.php
    Priority 1 — primary source; bdshare_announcements is dead (returns empty).

    Note: news_archive.php (old URL) returns 404. display_news.php loads
    content via JavaScript — Playwright required.
    Run `playwright install chromium` once before using.
    """
    name = "dse_direct_announcements"
    priority = 1
    timeout_seconds = 45

    def __init__(self, url: str = NEWS_URL) -> None:
        self._url = url

    def normalize(self, rows: list[dict[str, Any]]) -> pd.DataFrame:
        return _normalize_rows(rows, self.name)

    async def fetch(self, **kwargs: Any) -> AdapterResult:
        rows = await _playwright_fetch_news(self._url, self.name, self.timeout_seconds)

        if not rows:
            raise AdapterError(
                self.name,
                "no announcement rows extracted — page structure may have changed",
                retryable=False,
            )

        normalized = self.normalize(rows)
        return AdapterResult(
            data=normalized,
            source_name=self.name,
            fetched_at=datetime.now(timezone.utc),
            quality="ok",
            records=len(normalized),
            raw_sample=rows[0] if rows else {},
        )

    async def health_check(self) -> bool:
        try:
            result = await self.fetch()
            return result.records > 0
        except Exception:
            return False


class DSEDirectPSNAdapter(BaseAdapter):
    """
    DSE official site — price-sensitive news (last 7 days) via Playwright.

    URL: https://www.dsebd.org/news_archive_7days.php
    Priority 1 — primary source; bdshare_psn is dead (returns empty).

    Note: price_sensitive_news.php (old URL) returns 404. The 7-day archive
    contains PSN entries alongside general announcements; no dedicated PSN
    endpoint found on the current DSE site (confirmed 2026-05-21).
    """
    name = "dse_direct_psn"
    priority = 1
    timeout_seconds = 45

    def __init__(self, url: str = NEWS_7D_URL) -> None:
        self._url = url

    def normalize(self, rows: list[dict[str, Any]]) -> pd.DataFrame:
        return _normalize_rows(rows, self.name)

    async def fetch(self, **kwargs: Any) -> AdapterResult:
        rows = await _playwright_fetch_news(self._url, self.name, self.timeout_seconds)

        if not rows:
            raise AdapterError(
                self.name,
                "no PSN rows extracted — page structure may have changed",
                retryable=False,
            )

        normalized = self.normalize(rows)
        return AdapterResult(
            data=normalized,
            source_name=self.name,
            fetched_at=datetime.now(timezone.utc),
            quality="ok",
            records=len(normalized),
            raw_sample=rows[0] if rows else {},
        )

    async def health_check(self) -> bool:
        try:
            result = await self.fetch()
            return result.records > 0
        except Exception:
            return False
