"""DSE corporate announcement parser — extracts structured fields from disclosure text."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ParsedAnnouncement:
    announcement_type: str = "other"
    eps_value: Optional[float] = None
    eps_period: Optional[str] = None
    eps_type: Optional[str] = None
    dividend_cash_pct: Optional[float] = None
    dividend_stock_pct: Optional[float] = None
    dividend_year: Optional[int] = None


# ── Announcement type classification ──────────────────────────────────────────

_TYPE_KEYWORDS: list[tuple[str, list[str]]] = [
    # "earnings disclosure" = investor webinar event, NOT actual EPS data — excluded
    ("eps_disclosure",        ["eps", "earnings per share"]),
    ("dividend",              ["dividend"]),
    ("agm",                   ["agm", "annual general meeting"]),
    ("board_meeting",         ["board meeting"]),
    ("rights_issue",          ["rights issue", "rights share"]),
    ("suspension",            ["suspension"]),
    ("resumption",            ["resumption"]),
    ("auditor_qualification", ["emphasis of matter", "going concern", "qualified opinion"]),
    ("ipo",                   ["ipo", "public issue", "public offering"]),
    ("credit_rating",         ["credit rating"]),
]


def _classify_type(headline: str) -> str:
    h = headline.lower()
    for ann_type, keywords in _TYPE_KEYWORDS:
        if any(k in h for k in keywords):
            return ann_type
    return "other"


# ── EPS extraction ─────────────────────────────────────────────────────────────

# BDT/Tk amount after EPS context
_EPS_AMOUNT_RE = re.compile(
    r'(?:EPS|earnings per share|per share)[^.]{0,80}?(?:BDT|Tk\.?)\s*(\d{1,4}(?:\.\d{1,4})?)',
    re.IGNORECASE,
)
# BDT/Tk amount anywhere (fallback)
_AMOUNT_RE = re.compile(r'(?:BDT|Tk\.?)\s*(\d{1,4}(?:\.\d{1,4})?)', re.IGNORECASE)

# Quarter: "Q1 2026", "Q1-2026"
_QUARTER_RE = re.compile(r'\b(Q[1-4])[\s\-/]+(\d{4})\b', re.IGNORECASE)
# Half-year: "H1 2025"
_HALF_RE = re.compile(r'\b(H[12])[\s\-/]+(\d{4})\b', re.IGNORECASE)
# Month range: "January to March 2026"
_MONTH_RANGE_RE = re.compile(
    r'(January|February|March|April|May|June|July|August|September|October|November|December)'
    r'(?:\s+to\s+|\s*[-–]\s*)'
    r'(January|February|March|April|May|June|July|August|September|October|November|December)'
    r'\s+(\d{4})',
    re.IGNORECASE,
)
_MONTH_TO_QUARTER: dict[frozenset, str] = {
    frozenset({"january", "march"}):   "Q1",
    frozenset({"april", "june"}):      "Q2",
    frozenset({"july", "september"}):  "Q3",
    frozenset({"october", "december"}): "Q4",
    frozenset({"january", "june"}):    "H1",
    frozenset({"july", "december"}):   "H2",
}
# FY year
_FY_RE = re.compile(r'\bFY\s*(\d{4})\b', re.IGNORECASE)
_YEAR_ENDED_RE = re.compile(r'year\s+ended\s+(?:\w+\s+)?(\d{4})', re.IGNORECASE)


def _extract_eps(text: str) -> tuple[Optional[float], Optional[str], Optional[str]]:
    """Return (eps_value, eps_period, eps_type)."""
    # Value
    value: Optional[float] = None
    m = _EPS_AMOUNT_RE.search(text)
    if not m:
        m = _AMOUNT_RE.search(text)
    if m:
        try:
            value = float(m.group(1))
        except ValueError:
            pass

    # Period — priority: quarter > half > month-range > FY > year-ended
    period: Optional[str] = None
    if q := _QUARTER_RE.search(text):
        period = f"{q.group(1).upper()}_{q.group(2)}"
    elif h := _HALF_RE.search(text):
        period = f"{h.group(1).upper()}_{h.group(2)}"
    elif mr := _MONTH_RANGE_RE.search(text):
        m1 = mr.group(1).lower()
        m2 = mr.group(2).lower()
        yr = mr.group(3)
        label = _MONTH_TO_QUARTER.get(frozenset({m1, m2}))
        period = f"{label}_{yr}" if label else f"?_{yr}"
    elif fy := _FY_RE.search(text):
        period = f"FY{fy.group(1)}"
    elif ye := _YEAR_ENDED_RE.search(text):
        period = f"FY{ye.group(1)}"

    # Type
    tl = text.lower()
    if "unaudited" in tl:
        eps_type: Optional[str] = "unaudited"
    elif "audited" in tl:
        eps_type = "audited"
    else:
        eps_type = None

    return value, period, eps_type


# ── Dividend extraction ────────────────────────────────────────────────────────

_CASH_DIV_RES = [
    re.compile(r'[Cc]ash\s+[Dd]ividend\s*@?\s*(\d+(?:\.\d+)?)\s*%'),
    re.compile(r'(\d+(?:\.\d+)?)\s*%\s*[Cc]ash(?:\s+[Dd]ividend)?'),
    re.compile(r'[Cc]ash\s*:\s*(\d+(?:\.\d+)?)\s*%'),
    re.compile(r'[Dd]ividend\s*@\s*(\d+(?:\.\d+)?)\s*%'),   # "Dividend @15%"
]
_STOCK_DIV_RES = [
    re.compile(r'[Ss]tock\s+[Dd]ividend\s*@?\s*(\d+(?:\.\d+)?)\s*%'),
    re.compile(r'[Bb]onus\s+(?:[Ss]hares?\s*)?@?\s*(\d+(?:\.\d+)?)\s*%'),
    re.compile(r'(\d+(?:\.\d+)?)\s*%\s*(?:[Ss]tock|[Bb]onus)'),
]
_DIV_YEAR_RE = re.compile(
    r'(?:for\s+)?(?:the\s+)?(?:year|FY)\s*(?:ended\s+)?(?:\w+\s+)?(\d{4})',
    re.IGNORECASE,
)


def _first_match(patterns: list[re.Pattern], text: str) -> Optional[float]:
    for pat in patterns:
        m = pat.search(text)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def _extract_dividend(
    headline: str, details: str
) -> tuple[Optional[float], Optional[float], Optional[int]]:
    combined = f"{headline} {details}"
    cash_pct  = _first_match(_CASH_DIV_RES, combined)
    stock_pct = _first_match(_STOCK_DIV_RES, combined)

    year: Optional[int] = None
    if ym := _DIV_YEAR_RE.search(combined):
        try:
            year = int(ym.group(1))
        except ValueError:
            pass

    return cash_pct, stock_pct, year


# ── Content hash ───────────────────────────────────────────────────────────────

def content_hash(ticker: str, headline: str, published_at: str) -> str:
    raw = f"{ticker}|{headline}|{published_at}"
    return hashlib.md5(raw.encode()).hexdigest()


# ── Main entry point ───────────────────────────────────────────────────────────

def parse_announcement(
    ticker: str,
    headline: str,
    details: str,
    published_at: str,
) -> ParsedAnnouncement:
    ann_type = _classify_type(headline)
    combined = f"{headline} {details}".lower()

    # EPS: extract regardless of type — DSE puts EPS values inside dividend
    # continuation announcements too (e.g. "EPS of Tk. 21.90 for FY2025").
    eps_value = eps_period = eps_type = None
    if any(k in combined for k in ("eps", "earnings per share", "earning per share")):
        eps_value, eps_period, eps_type = _extract_eps(f"{headline} {details}")

    div_cash = div_stock = div_year = None
    if ann_type == "dividend":
        div_cash, div_stock, div_year = _extract_dividend(headline, details)

    return ParsedAnnouncement(
        announcement_type=ann_type,
        eps_value=eps_value,
        eps_period=eps_period,
        eps_type=eps_type,
        dividend_cash_pct=div_cash,
        dividend_stock_pct=div_stock,
        dividend_year=div_year,
    )
