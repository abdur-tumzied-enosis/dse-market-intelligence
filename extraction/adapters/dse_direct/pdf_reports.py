from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import pandas as pd

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import normalize_ticker

# Confirmed 2026-05-21 (reconfirmed 2026-05-21 via full site exploration):
# - companyinfo.php?reqType=financials&cname={ticker} returns 404
# - displayCompany.php?name={ticker} (full JS render via Playwright) contains only
#   inline financial data (EPS/NAV/dividends) and DSE regulatory PDFs.
#   NO company-specific annual report PDFs hosted on dsebd.org.
# - BSEC (sec.gov.bd) is a regulatory body website — no queryable company
#   annual report portal exists there either.
# - DSE announcements (display_news.php) contain no PDF attachments.
#
# Annual report PDFs are hosted on individual company investor relations pages
# (e.g. bracbank.com/en/investor-relations). There is no centralized source.
# Building a generic annual_reports_pdf adapter requires per-company IR page
# scrapers — out of scope for Phase 1E+.
#
# This adapter is kept as a documented stub so the annual_reports_pdf stream
# has a registered adapter (required for registry integrity) and will produce
# a clear error message if invoked.

COMPANY_URL_TPL = "https://old.dsebd.org/displayCompany.php?name={ticker}"
DSE_BASE = "https://old.dsebd.org"

_YEAR_RE = re.compile(r"(20\d{2})", re.IGNORECASE)
_REGULATORY_KEYWORDS = {
    "demutualization", "dsex", "ds30", "dses", "memorandum",
    "settlement", "short-sale", "auto_trade", "bhoms", "gri",
    "facilitiesfor", "introduction", "iMDS", "eod", "bangladesh bank policy",
}


def _is_regulatory_pdf(url: str, text: str) -> bool:
    combined = (url + " " + text).lower()
    return any(k in combined for k in _REGULATORY_KEYWORDS)


def _classify_report(url: str, text: str) -> str:
    combined = (url + " " + text).lower()
    if re.search(r"annual|ar\d{4}|ann_rep", combined):
        return "annual"
    if re.search(r"q[1-4]|quarter", combined):
        return "quarterly"
    if re.search(r"half|interim|h[12]\d{4}", combined):
        return "interim"
    return "financial_statement"


def _extract_year(url: str, text: str) -> int | None:
    m = _YEAR_RE.search(url) or _YEAR_RE.search(text)
    return int(m.group(1)) if m else None


class DSEDirectPDFAdapter(BaseAdapter):
    """
    DSE official site — company annual report PDF links.

    STUB — see module docstring. DSE does not host company annual reports.
    Invoking fetch() raises AdapterError(retryable=False) with a clear message
    directing to BSEC (sec.gov.bd) as the correct source.

    Priority 1 — only adapter registered for annual_reports_pdf stream.
    Replace this with BsecPDFAdapter when implemented (Phase 1E+).
    """
    name = "dse_direct_pdf"
    priority = 1
    timeout_seconds = 45

    def __init__(self, base_url: str = DSE_BASE) -> None:
        self._base = base_url

    def normalize(self, links: list[dict[str, Any]], ticker: str) -> pd.DataFrame:
        rows = []
        seen: set[str] = set()
        for item in links:
            url = item["url"]
            if url in seen or _is_regulatory_pdf(url, item.get("text", "")):
                continue
            seen.add(url)
            text = item.get("text", "")
            filename = url.split("/")[-1]
            rows.append({
                "ticker":      normalize_ticker(ticker),
                "report_type": _classify_report(url, text),
                "year":        _extract_year(url, text),
                "filename":    filename,
                "url":         url,
                "link_text":   text.strip(),
                "fetched_at":  datetime.now(timezone.utc),
                "source":      self.name,
            })
        return pd.DataFrame(rows)

    async def fetch(self, ticker: str, **kwargs: Any) -> AdapterResult:
        raise AdapterError(
            self.name,
            (
                f"No centralized annual report PDF source exists for DSE companies "
                f"(confirmed 2026-05-21). DSE and BSEC do not host company PDFs. "
                f"Annual reports for {ticker} are on the company's own investor "
                f"relations page. Per-company IR scrapers needed to resolve this stream."
            ),
            retryable=False,
        )

    async def health_check(self) -> bool:
        return False   # known unavailable; don't waste a Playwright call
