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

NEWS_URL     = "https://www.dsebd.org/display_news.php"
NEWS_7D_URL  = "https://www.dsebd.org/news_archive_7days.php"

# CSS selectors to try for the rendered news table / list
_TABLE_SEL   = "table"
_ROW_SEL     = "tr"
_TICKER_ATTR = "href"   # anchor href contains ticker, e.g. displayCompany.php?name=BRACBANK

import re
_TICKER_FROM_HREF = re.compile(r"displayCompany\.php\?name=([^&\"']+)", re.IGNORECASE)
_DATE_RE          = re.compile(r"\d{2}-\w{3}-\d{4}|\d{4}-\d{2}-\d{2}")


def _extract_ticker_from_cell(cell_html: str) -> str:
    """Pull ticker from anchor href in cell HTML if present."""
    m = _TICKER_FROM_HREF.search(cell_html)
    return normalize_ticker(m.group(1)) if m else ""


async def _playwright_fetch_news(url: str, adapter_name: str, timeout_s: int) -> list[dict[str, Any]]:
    """
    Render a DSE news page with Playwright and extract announcement rows.

    The page loads news content dynamically via JS. After networkidle we look
    for tables with date + ticker + headline columns.

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

            # Wait for actual content cells (not just nav links)
            try:
                await page.wait_for_selector("table td", timeout=15_000)
            except Exception:
                pass

            # Grab the RightBody div HTML (contains the news table)
            right_body = await page.query_selector("#RightBody")
            content_html = await right_body.inner_html() if right_body else await page.content()

            # Parse tables within the content
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(content_html, "lxml")

            rows: list[dict[str, Any]] = []
            for table in soup.find_all("table"):
                tr_list = table.find_all("tr")
                # Need at least header + 1 data row, and cells containing date patterns
                if len(tr_list) < 2:
                    continue
                for tr in tr_list[1:]:  # skip header row
                    cells = tr.find_all(["td", "th"])
                    if not cells:
                        continue
                    cell_texts = [c.get_text(strip=True) for c in cells]
                    cell_htmls = [str(c) for c in cells]

                    # Try to find date and ticker in the row
                    date_str = ""
                    for txt in cell_texts:
                        if _DATE_RE.search(txt):
                            date_str = txt
                            break

                    ticker = ""
                    for html_frag in cell_htmls:
                        t = _extract_ticker_from_cell(html_frag)
                        if t:
                            ticker = t
                            break
                    # Also try text that looks like a ticker (all-caps, 3-12 chars)
                    if not ticker:
                        for txt in cell_texts:
                            if re.fullmatch(r"[A-Z0-9()&\-]{2,15}", txt.strip()):
                                ticker = normalize_ticker(txt.strip())
                                break

                    if not ticker and not date_str:
                        continue  # not a news row

                    # Remaining non-date, non-ticker cells → headline / details
                    non_key_texts = [
                        t for t in cell_texts
                        if t != date_str and t != ticker and len(t) > 3
                    ]
                    headline = non_key_texts[0] if non_key_texts else ""
                    details  = non_key_texts[1] if len(non_key_texts) > 1 else ""

                    rows.append({
                        "ticker":    ticker,
                        "date_str":  date_str,
                        "headline":  headline,
                        "details":   details,
                        "category":  "",
                    })

            await browser.close()
            return rows

    except AdapterError:
        raise
    except Exception as exc:
        raise AdapterError(adapter_name, f"Playwright failed: {exc}", retryable=True) from exc


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
    Priority 2 — activates when bdshare_announcements fails.

    Note: news_archive.php (old URL) returns 404. display_news.php loads
    content via JavaScript — Playwright required.
    Run `playwright install chromium` once before using.
    """
    name = "dse_direct_announcements"
    priority = 2
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
    Priority 2 — activates when bdshare_psn fails.

    Note: price_sensitive_news.php (old URL) returns 404. The 7-day archive
    contains PSN entries alongside general announcements; no dedicated PSN
    endpoint found on the current DSE site (confirmed 2026-05-21).
    """
    name = "dse_direct_psn"
    priority = 2
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
