from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import normalize_ticker, to_decimal

# Confirmed URL (2026-05-21): dse_graph_chart.php returns 404.
# mkt_depth_3.php is the active market depth page.
DEPTH_URL = "https://old.dsebd.org/mkt_depth_3.php"

# Auth note: DSE requires DSE-Mobile / M-invest login for full bid/ask order book
# (per site notice). Without auth, the page loads the instrument selector and
# price statistics (open, close, last trade, trade count) but the bid/ask cells
# are empty. This adapter extracts whatever is available: price stats + any
# bid/ask prices that load without auth.


async def _rows_from_elements(row_handles: list[Any]) -> list[tuple[str, str]]:
    """Extract (col0, col1) text from a list of <tr> Playwright element handles."""
    result = []
    for row in row_handles:
        cells = await row.query_selector_all("td")
        if len(cells) >= 2:
            price = (await cells[0].inner_text()).strip()
            vol   = (await cells[1].inner_text()).strip()
            result.append((price, vol))
    return result


class DSEDirectDepthPlaywrightAdapter(BaseAdapter):
    """
    DSE official site — market depth via Playwright.

    URL: https://old.dsebd.org/mkt_depth_3.php
    Priority 2 — fallback when bdshare_depth fails.

    Auth limitation (confirmed 2026-05-21): DSE requires DSE-Mobile app or
    M-invest account registration for full 10-level bid/ask order book. Without
    auth, this adapter extracts Price Statistics (open, close, LTP, trade count)
    as a partial result. quality="partial" reflects this.

    If auth credentials are added to .env (DSE_USERNAME / DSE_PASSWORD) and a
    login step is wired in here, full depth becomes available.
    """
    name = "dse_direct_depth"
    priority = 2
    timeout_seconds = 45

    def normalize(self, raw: Any) -> pd.DataFrame:
        """Satisfy BaseAdapter ABC; actual normalization is in normalize_price_stats."""
        if isinstance(raw, dict):
            return self.normalize_price_stats(raw, raw.get("ticker", ""))
        return pd.DataFrame()

    def normalize_price_stats(self, stats: dict[str, str], ticker: str) -> pd.DataFrame:
        row: dict[str, Any] = {
            "ticker":       normalize_ticker(ticker),
            "fetched_at":   datetime.now(timezone.utc),
            "source":       self.name,
            "auth_required": True,   # flag: full depth needs DSE login
        }
        # Map price-stats labels to canonical names
        label_map = {
            "Open Price":            "open",
            "Last Trade Price":      "close",
            "Yesterday Close Price": "prev_close",
            "Day's High":           "high",
            "Day's Low":            "low",
            "No. of Trade":         "trades",
        }
        for label, value in stats.items():
            canonical = label_map.get(label)
            if canonical:
                row[canonical] = to_decimal(value.lstrip(":").strip())

        # Stub out bid/ask columns so schema is consistent with bdshare_depth
        for i in range(1, 6):
            row.setdefault(f"bid_price_{i}", None)
            row.setdefault(f"bid_vol_{i}", None)
            row.setdefault(f"ask_price_{i}", None)
            row.setdefault(f"ask_vol_{i}", None)

        return pd.DataFrame([row])

    async def _extract_price_stats(self, page: Any) -> dict[str, str]:
        """Extract key/value pairs from the Price Statistics table."""
        from bs4 import BeautifulSoup
        content = await page.content()
        soup = BeautifulSoup(content, "lxml")
        stats: dict[str, str] = {}
        for tbl in soup.find_all("table"):
            rows = tbl.find_all("tr")
            for row in rows:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                # Rows like: ['Open Price', ': 239.8', "Day's High :", '239.8']
                if len(cells) == 4:
                    stats[cells[0].rstrip(":")] = cells[1]
                    stats[cells[2].rstrip(":")] = cells[3]
                elif len(cells) == 2 and ":" in cells[1]:
                    stats[cells[0]] = cells[1]
        return stats

    async def fetch(self, ticker: str, **kwargs: Any) -> AdapterResult:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise AdapterError(self.name, "playwright not installed", retryable=False) from exc

        ticker = normalize_ticker(ticker)

        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(DEPTH_URL, wait_until="networkidle", timeout=self.timeout_seconds * 1000)

                # Select ticker from the inst dropdown
                try:
                    await page.select_option("select[name=inst]", ticker)
                    await page.wait_for_timeout(3000)
                except Exception as exc:
                    raise AdapterError(
                        self.name,
                        f"could not select ticker {ticker!r} from dropdown: {exc}",
                        retryable=True,
                    ) from exc

                stats = await self._extract_price_stats(page)
                await browser.close()

        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(self.name, f"Playwright failed for {ticker}: {exc}", retryable=True) from exc

        if not stats:
            raise AdapterError(
                self.name,
                f"no price stats found for {ticker} — instrument may not be in dropdown",
                retryable=False,
            )

        normalized = self.normalize_price_stats(stats, ticker)
        return AdapterResult(
            data=normalized,
            source_name=self.name,
            fetched_at=datetime.now(timezone.utc),
            # partial: bid/ask unavailable without DSE auth; price stats only
            quality="partial",
            records=1,
            raw_sample=stats,
        )

    async def health_check(self) -> bool:
        try:
            result = await self.fetch(ticker="GP")
            return result.records > 0
        except Exception:
            return False
