from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

import httpx
import pandas as pd
from bs4 import BeautifulSoup

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import normalize_ticker, to_decimal

COMPANY_URL = "https://www.dsebd.org/displayCompany.php?name={ticker}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.dsebd.org/",
}

# Confirmed structure (2026-05-23) via HTML inspection:
#
# 1. th/td pairs — market data + capital structure + sector:
#    <tr><th>Sector</th><td>Telecommunication</td></tr>
#    <th>Authorized Capital (mn)</th><td>40,000.00</td>
#    <th>Paid-up Capital (mn)</th><td>13,503.00</td>
#    <th>Market Capitalization (mn)</th><td>320,966.315</td>
#    <th>Reserve & Surplus without OCI (mn)</th><td>35,167.4</td>
#    <th>Year End</th><td>31-Dec</td>
#    <th>Cash Dividend</th><td>215% 2025, 330% 2024, ...</td>
#
# 2. td/td table (id varies) — listing info:
#    <tr><td>Listing Year</td><td>2009</td></tr>
#    <tr><td>Market Category</td><td>A</td></tr>
#
# 3. Multi-year EPS+NAV table (8 rows):
#    headers: Year | EPS Basic | Diluted | ... | NAV Per Share | ...
#    data rows: 2021, 2022, ... 2025
#    → take last non-empty row
#
# 4. P/E + Dividend Yield table (9 rows):
#    headers: Year | P/E using Basic EPS | ... | Dividend in % | Dividend Yield in %
#    → take last data row
#
# 5. Quarterly EPS table (11 rows):
#    headers: Particulars | Q1 | Q2 | Half Yearly | Q3 | 9 Months | Annual
#    row "Basic" under "Earnings Per Share (EPS)" section
#    → most recent year's Q1-Q3
#
# 6. Shareholding — nested td with inline "Label:<br>value" pattern:
#    Sponsor/Director:<br>90.00  Govt:<br>0.00  Institute:<br>6.63 ...
#    Shareholding date from th label containing "as on"


def _parse_th_td(soup: BeautifulSoup) -> dict[str, str]:
    """Extract th→td key-value pairs. Handles multiple th/td per row."""
    kv: dict[str, str] = {}
    _MAP = {
        "sector":                             "sector",
        "authorized capital (mn)":           "authorized_cap_mn",
        "paid-up capital (mn)":              "paid_up_cap_mn",
        "total no. of outstanding securities": "total_securities",
        "market capitalization (mn)":        "market_cap_mn",
        "free float market cap. (mn)":       "free_float_cap_mn",
        "reserve & surplus without oci (mn)": "reserve_surplus_mn",
        "year end":                           "fiscal_year_end",
        "cash dividend":                     "dividend_raw",
        "bonus issue (stock dividend)":      "bonus_raw",
        "last trading price":               "ltp",
        "details of financial statement":   "ir_url",
    }
    for row in soup.find_all("tr"):
        ths = row.find_all("th")
        tds = row.find_all("td")
        if ths:
            for th, td in zip(ths, tds):
                label = th.get_text(strip=True).lower().rstrip("*").strip()
                if label in _MAP:
                    kv[_MAP[label]] = td.get_text(strip=True)
    return kv


def _parse_td_td(soup: BeautifulSoup) -> dict[str, str]:
    """Extract td→td pairs for simple 2-column info tables (Listing Year, Market Category, etc.)."""
    kv: dict[str, str] = {}
    _MAP = {
        "listing year":    "listing_year",
        "market category": "market_category",
        "address":         "address",
        "phone":           "phone",
        "email":           "email",
        "web":             "web",
    }
    for row in soup.find_all("tr"):
        tds = row.find_all("td")
        if len(tds) >= 2:
            label = tds[0].get_text(strip=True).lower().rstrip(":").strip()
            if label in _MAP:
                kv[_MAP[label]] = tds[1].get_text(strip=True)
    return kv


def _find_eps_nav_table(soup: BeautifulSoup) -> Any:
    """The annual EPS/NAV/profit table — identified by its header text."""
    for tbl in soup.find_all("table"):
        head = " ".join(c.get_text(strip=True) for c in tbl.find_all(["th", "td"])[:14]).lower()
        if "nav per share" in head and "year" in head:
            return tbl
    return None


def _expand_header_grid(table: Any) -> list[str]:
    """Flatten a table's multi-row colspan/rowspan header into one hierarchical
    label per leaf column, e.g. 'earnings per share(eps) > basic > original'.

    Header rows are everything above the first row whose first cell is a
    4-digit year (or 'particulars' data marker)."""
    if table is None:
        return []
    header_rows: list[list[Any]] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        if re.match(r"^\d{4}$", cells[0].get_text(strip=True)):
            break  # data rows reached
        header_rows.append(cells)
    if not header_rows:
        return []

    grid: dict[tuple[int, int], str] = {}
    for r, cells in enumerate(header_rows):
        c = 0
        for cell in cells:
            while (r, c) in grid:
                c += 1
            text = cell.get_text(" ", strip=True).lower()
            try:
                rowspan = int(cell.get("rowspan") or 1)
                colspan = int(cell.get("colspan") or 1)
            except ValueError:
                rowspan = colspan = 1
            for dr in range(rowspan):
                for dc in range(colspan):
                    grid[(r + dr, c + dc)] = text
            c += colspan

    n_cols = max(c for (_, c) in grid) + 1
    labels: list[str] = []
    for c in range(n_cols):
        parts: list[str] = []
        for r in range(len(header_rows)):
            t = grid.get((r, c), "")
            if t and (not parts or parts[-1] != t):
                parts.append(t)
        labels.append(" > ".join(parts))
    return labels


def _grid_cell(cells: list[str], grid_idx: int | None, col_offset: int) -> Any:
    """Value at a header-grid column, mapped to the data row (the 'Year' header
    often spans 2 grid columns while data rows have one year cell — offset
    corrects the index). None for '-', '', 'N/A', or out-of-range."""
    if grid_idx is None:
        return None
    idx = grid_idx - col_offset
    if idx < 0 or idx >= len(cells):
        return None
    v = cells[idx].replace(",", "").strip()
    return to_decimal(v) if v not in ("-", "", "N/A") else None


def _year_col_offset(labels: list[str]) -> int:
    """Grid→data index offset for the Year column.

    The 'Year' header cell often has colspan=2 while each data row contains
    only one year cell.  The offset corrects grid column indices so they map
    to the correct data-row cell index."""
    year_grid_cols = sum(1 for lab in labels if lab == "year")
    return max(year_grid_cols - 1, 0)


def _find_col(labels: list[str], *needle_sets: tuple[str, ...]) -> int | None:
    """Index of the first label containing ALL needles; needle sets tried in
    preference order. None when nothing matches."""
    for needles in needle_sets:
        for i, lab in enumerate(labels):
            if all(n in lab for n in needles):
                return i
    return None


def _parse_eps_nav_table(soup: BeautifulSoup) -> dict[str, Any]:
    """
    Annual EPS+NAV table. Headers: Year | EPS Basic | Diluted | ... | NAV Per Share
    Take the most recent year's row.
    """
    result: dict[str, Any] = {"eps": None, "nav": None}
    for tbl in soup.find_all("table"):
        headers = [td.get_text(strip=True) for td in tbl.find_all(["th", "td"])[:12]]
        header_str = " ".join(headers).lower()
        if "nav per share" in header_str and "year" in header_str:
            # find column indices from flattened header rows
            all_rows = tbl.find_all("tr")
            data_rows = []
            for row in all_rows:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                # data rows start with a 4-digit year
                if cells and re.match(r"^\d{4}$", cells[0]):
                    data_rows.append(cells)

            if data_rows:
                last = data_rows[-1]  # most recent year
                # column layout (confirmed GP 2025):
                # 0=Year, 1=EPS_basic_orig, 2=EPS_basic_rest, 3=EPS_diluted_orig, 4=EPS_diluted_rest,
                # 5=EPS_cont_basic_orig, 6=EPS_cont_basic_rest, 7=NAV_orig, 8=NAV_rest, ...
                # Use the first non-dash numeric value for EPS (prefer diluted restated = col 4)
                def _first_num(cells: list[str], indices: list[int]) -> str:
                    for i in indices:
                        if i < len(cells) and cells[i] not in ("-", "", "N/A"):
                            return cells[i]
                    return ""

                result["eps"] = to_decimal(_first_num(last, [4, 3, 2, 1]))
                result["nav"] = to_decimal(_first_num(last, [8, 7]))
            break
    return result


def _parse_pe_dividend_table(soup: BeautifulSoup) -> dict[str, Any]:
    """
    P/E + Dividend Yield table. Headers: Year | P/E ... | Dividend in % | Dividend Yield in %
    Take the most recent year's row.
    """
    result: dict[str, Any] = {"pe_audited": None, "dividend_yield": None}
    for tbl in soup.find_all("table"):
        headers = " ".join(td.get_text(strip=True) for td in tbl.find_all(["th", "td"])[:15]).lower()
        if "dividend yield" in headers and "year" in headers:
            all_rows = tbl.find_all("tr")
            data_rows = []
            for row in all_rows:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if cells and re.match(r"^\d{4}$", cells[0]):
                    data_rows.append(cells)
            if data_rows:
                last = data_rows[-1]
                # columns: 0=Year, 1=P/E basic orig, 2=P/E basic rest, 3=P/E diluted orig, 4=P/E diluted rest,
                #          5=P/E cont basic orig, 6=P/E cont basic rest, 7=Dividend%, 8=Dividend yield%
                def _first_num(cells: list[str], indices: list[int]) -> str:
                    for i in indices:
                        if i < len(cells) and cells[i] not in ("-", "", "N/A"):
                            return cells[i]
                    return ""

                result["pe_audited"]    = to_decimal(_first_num(last, [4, 3, 2, 1]))
                result["dividend_yield"] = to_decimal(_first_num(last, [8, 7]))
            break
    return result


def _parse_quarterly_eps(soup: BeautifulSoup) -> dict[str, Any]:
    """
    Quarterly EPS table. Columns: Q1 | Q2 | Half Yearly | Q3 | 9 Months | Annual
    Returns most-recently-reported quarterly values.
    """
    result: dict[str, Any] = {"eps_q1": None, "eps_q2": None, "eps_q3": None, "eps_q4": None}
    for tbl in soup.find_all("table"):
        headers_text = " ".join(td.get_text(strip=True) for td in tbl.find_all(["th", "td"])[:10]).lower()
        if "q1" in headers_text and "q2" in headers_text and "half yearly" in headers_text:
            in_eps_section = False
            for row in tbl.find_all("tr"):
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if not cells:
                    continue
                row_label = cells[0].lower()
                if "earnings per share" in row_label:
                    in_eps_section = True
                    continue
                if in_eps_section and row_label == "basic":
                    # columns after label: Q1, Q2, Half Yearly, Q3, 9 Months, Annual
                    def _val(cells: list[str], idx: int) -> Any:
                        v = cells[idx].strip() if idx < len(cells) else "-"
                        return to_decimal(v) if v not in ("-", "", "N/A") else None
                    result["eps_q1"] = _val(cells, 1)
                    result["eps_q2"] = _val(cells, 2)
                    result["eps_q3"] = _val(cells, 4)  # col 3=half-yearly, col 4=Q3
                    result["eps_q4"] = _val(cells, 6) if len(cells) > 6 else None
                    break
            break
    return result


def _parse_eps_nav_all_years(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """All year rows from the EPS+NAV table via header-grid column mapping.
    → [{fiscal_year, eps, eps_basis, eps_diluted, nav, net_profit_mn, tci_mn}, ...]

    Table absent (legitimately missing on sparse pages) → returns [].
    Table found but grid unparseable → raises ValueError (layout change).

    CO-fallback rationale: on current DSE layouts the plain-EPS columns are
    often all '-' and real values live under 'EPS - Continuing Operations'
    columns; the candidate list handles this via fallback, matching the
    Task 4 approach."""
    tbl = _find_eps_nav_table(soup)
    if tbl is None:
        return []
    labels = _expand_header_grid(tbl)
    if not labels:
        raise ValueError("EPS/NAV table header grid unparseable — layout changed?")

    col_offset = _year_col_offset(labels)

    # (column, basis-tag) preference: restated over original, basic before diluted,
    # non-continuing before continuing-operations columns.
    # 'continuing operations' columns have label prefix 'eps - continuing operations'.
    eps_candidates = [
        (("earnings per share", "basic", "restated"), "basic_restated"),
        (("earnings per share", "basic", "original"), "basic_original"),
        (("earnings per share", "diluted", "restated"), "diluted_restated"),
        (("earnings per share", "diluted", "original"), "diluted_original"),
        (("earnings per share", "diluted"), "diluted"),
        (("continuing operations", "basic", "restated"), "co_basic_restated"),
        (("continuing operations", "basic", "original"), "co_basic_original"),
        (("continuing operations", "diluted"), "co_diluted"),
    ]
    dil_col = _find_col(labels,
                        ("earnings per share", "diluted", "restated"),
                        ("earnings per share", "diluted", "original"),
                        ("earnings per share", "diluted"))
    # NAV: prefer original (most commonly populated), then restated, then any nav per share
    nav_col = _find_col(labels,
                        ("nav per share", "original"),
                        ("nav per share", "restated"),
                        ("nav per share",))
    profit_col = _find_col(labels, ("profit for the year",))
    tci_col = _find_col(labels, ("tci",))

    rows_out: list[dict[str, Any]] = []
    for row in tbl.find_all("tr"):
        cells = [td.get_text(strip=True) for td in row.find_all("td")]
        if not (cells and re.match(r"^\d{4}$", cells[0])):
            continue
        eps_val, eps_basis = None, None
        for needles, basis in eps_candidates:
            v = _grid_cell(cells, _find_col(labels, needles), col_offset)
            if v is not None:
                eps_val, eps_basis = v, basis
                break
        rows_out.append({
            "fiscal_year":   int(cells[0]),
            "eps":           eps_val,
            "eps_basis":     eps_basis,
            "eps_diluted":   _grid_cell(cells, dil_col, col_offset),   # D3: no basic fallback
            "nav":           _grid_cell(cells, nav_col, col_offset),
            "net_profit_mn": _grid_cell(cells, profit_col, col_offset),  # D1 fix
            "tci_mn":        _grid_cell(cells, tci_col, col_offset),
        })
    return rows_out


_DIV_HIST_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(\d{4})")


def _find_pe_dividend_table(soup: BeautifulSoup) -> Any:
    """The P/E + Dividend Yield table — identified by header text."""
    for tbl in soup.find_all("table"):
        head = " ".join(c.get_text(strip=True) for c in tbl.find_all(["th", "td"])[:20]).lower()
        if "dividend yield" in head and "year" in head:
            return tbl
    return None


def _parse_pe_dividend_all_years(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """
    All year rows from P/E + Dividend Yield table via header-grid column mapping.
    Returns [{fiscal_year, pe, dividend_yield}, ...]

    The 'Year' header cell has colspan=2 while each data row has a single year
    cell — identical to the EPS/NAV table pattern.  col_offset corrects the
    grid→data-index translation (mirrors _parse_eps_nav_all_years).

    PE preference order (restated > original, main EPS > continuing operations):
      1. earnings per share(eps) > using diluted eps
      2. earnings per share(eps) > using basic eps > restated
      3. earnings per share(eps) > using basic eps > original
      4. eps - continuing operations > using basic eps > original
      5. eps - continuing operations > using basic eps > restated
      6. eps - continuing operations > using diluted eps

    Note: on some layouts (e.g. CITYBANK) the main-EPS columns are all '-'
    and values live exclusively in the continuing-operations columns; the
    candidate list handles this via fallback, matching the Task 4 approach.
    """
    tbl = _find_pe_dividend_table(soup)
    if tbl is None:
        return []

    labels = _expand_header_grid(tbl)
    if not labels:
        raise ValueError("P/E Dividend table header grid unparseable — layout changed?")

    col_offset = _year_col_offset(labels)

    # PE preference: restated > original, main EPS section > continuing operations
    pe_candidates = [
        ("earnings per share(eps)", "using diluted eps"),
        ("earnings per share(eps)", "using basic eps", "restated"),
        ("earnings per share(eps)", "using basic eps", "original"),
        ("eps - continuing operations", "using basic eps", "original"),
        ("eps - continuing operations", "using basic eps", "restated"),
        ("eps - continuing operations", "using diluted eps"),
    ]
    div_yield_col = _find_col(labels, ("dividend yield",))

    rows_out = []
    for row in tbl.find_all("tr"):
        cells = [td.get_text(strip=True) for td in row.find_all("td")]
        if not (cells and re.match(r"^\d{4}$", cells[0])):
            continue

        pe_val = None
        for needles in pe_candidates:
            v = _grid_cell(cells, _find_col(labels, needles), col_offset)
            if v is not None:
                pe_val = v
                break

        rows_out.append({
            "fiscal_year":    int(cells[0]),
            "pe":             pe_val,
            "dividend_yield": _grid_cell(cells, div_yield_col, col_offset),
        })

    return rows_out


def _parse_dividend_history_th_td(
    cash_raw: str | None,
    bonus_raw: str | None,
) -> dict[int, dict[str, Any]]:
    """
    Parse th/td dividend history strings into per-year dicts.
    Input:  "12.50% 2024, 10% 2023, 7.50% 2022"
    Output: {2024: {"cash": 12.5, "bonus": None}, 2023: {"cash": 10.0, ...}, ...}
    """
    by_year: dict[int, dict[str, Any]] = {}
    for m in _DIV_HIST_RE.finditer(cash_raw or ""):
        yr = int(m.group(2))
        by_year.setdefault(yr, {"cash": None, "bonus": None})
        by_year[yr]["cash"] = float(m.group(1))
    for m in _DIV_HIST_RE.finditer(bonus_raw or ""):
        yr = int(m.group(2))
        by_year.setdefault(yr, {"cash": None, "bonus": None})
        by_year[yr]["bonus"] = float(m.group(1))
    return by_year


def _merge_yearly_rows(
    eps_rows: list[dict],
    pe_rows: list[dict],
    div_by_year: dict[int, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Merge EPS+NAV, PE, and th/td dividend history rows by fiscal_year.

    D2 fix: dividend-only years (present in div_by_year but absent from both
    eps_map and pe_map) are now included in the output.  Previously the union
    was eps_map | pe_map, which silently dropped years like CITYBANK's 2004
    bonus-only rows that predate the EPS/PE table range.
    """
    eps_map = {r["fiscal_year"]: r for r in eps_rows}
    pe_map  = {r["fiscal_year"]: r for r in pe_rows}
    div_map = div_by_year or {}
    # Include dividend-only years (D2 fix)
    years   = sorted(set(eps_map) | set(pe_map) | set(div_map))
    out = []
    for yr in years:
        e = eps_map.get(yr, {})
        p = pe_map.get(yr, {})
        d = div_map.get(yr, {})
        out.append({
            "fiscal_year":   yr,
            "eps":           e.get("eps"),
            "eps_basis":     e.get("eps_basis"),
            "eps_diluted":   e.get("eps_diluted"),
            "nav":           e.get("nav"),
            "net_profit_mn": e.get("net_profit_mn"),
            "tci_mn":        e.get("tci_mn"),
            "pe":            p.get("pe"),
            "dividend_yield": p.get("dividend_yield"),
            "cash_div_pct":  d.get("cash"),
            "stock_div_pct": d.get("bonus"),
        })
    return out


def _parse_shareholding(soup: BeautifulSoup) -> dict[str, Any]:
    """
    Shareholding rows: <tr> with first <td> containing 'Share Holding Percentage [as on ...]'
    Sibling tds at index 2-6: 'Sponsor/Director:90.00', 'Govt:0.00', 'Institute:6.63', 'Foreign:0.80', 'Public:2.57'
    Three period rows exist; take the LAST (most recent).
    """
    result: dict[str, Any] = {
        "sponsor_pct": None, "govt_pct": None,
        "institution_pct": None, "foreign_pct": None, "public_pct": None,
        "shareholding_date": None,
    }
    _label_map = {
        "sponsor/director": "sponsor_pct",
        "govt":             "govt_pct",
        "government":       "govt_pct",
        "institute":        "institution_pct",
        "foreign":          "foreign_pct",
        "public":           "public_pct",
    }
    share_rows = []
    for td in soup.find_all("td"):
        txt = td.get_text(strip=True)
        if "Share Holding" in txt and "as on" in txt.lower():
            row = td.find_parent("tr")
            if row:
                share_rows.append(row)

    if not share_rows:
        return result

    # Take last row = most recent period
    row = share_rows[-1]
    tds = row.find_all("td")

    # Date from first td
    date_m = re.search(r"as on\s+(.+?)[\]\n]", tds[0].get_text(strip=True), re.IGNORECASE)
    if date_m:
        result["shareholding_date"] = date_m.group(1).strip()

    # Values from tds[2:] — each td text like "Sponsor/Director:90.00"
    for td in tds[2:]:
        text = td.get_text(strip=True)
        if ":" in text:
            parts = text.split(":", 1)
            key = parts[0].strip().lower()
            val = parts[1].strip().split()[0] if parts[1].strip() else ""
            if key in _label_map and val:
                result[_label_map[key]] = to_decimal(val)

    return result


# ---------------------------------------------------------------------------
# Task 6 helpers — date parsing, shareholding history, right issues,
# status table, links, quarterly EPS (full grid)
# ---------------------------------------------------------------------------

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Matches "Dec 31, 2025" or "December 31, 2025" or "31 Dec 2025" etc.
_AS_ON_RE = re.compile(
    r"(?:(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4}))"   # dd Mon yyyy
    r"|(?:([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4}))",  # Mon dd yyyy
    re.IGNORECASE,
)

# Matches right issue strings like "1R:1", "1R:2"
# Format on page: "NR:D YYYY" where N=numerator, D=denominator
_RIGHT_RE = re.compile(r"(\d+)R:(\d+)\s+(\d{4})", re.IGNORECASE)


def _parse_as_on_date(text: str) -> date | None:
    """Parse a date string into a date object.

    Accepts formats like "Dec 31, 2025", "December 31, 2025", "31 Dec 2025".
    Returns None for unparseable input.
    """
    m = _AS_ON_RE.search(text)
    if not m:
        return None
    # Group 1-3: dd Mon yyyy  /  Group 4-6: Mon dd yyyy
    if m.group(1):
        day, mon_str, yr = int(m.group(1)), m.group(2).lower()[:3], int(m.group(3))
    else:
        mon_str, day, yr = m.group(4).lower()[:3], int(m.group(5)), int(m.group(6))
    month = _MONTHS.get(mon_str)
    if not month:
        return None
    try:
        return date(yr, month, day)
    except ValueError:
        return None


def _parse_shareholding_all(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Return ALL shareholding period rows (oldest → newest) as a list of dicts.

    Each dict has keys: shareholding_date (date | None), sponsor_pct,
    govt_pct, institution_pct, foreign_pct, public_pct.

    Unlike _parse_shareholding (which keeps only the last row), this returns
    every row so callers can populate multi-period columns.
    """
    _label_map = {
        "sponsor/director": "sponsor_pct",
        "govt":             "govt_pct",
        "government":       "govt_pct",
        "institute":        "institution_pct",
        "foreign":          "foreign_pct",
        "public":           "public_pct",
    }
    share_rows = []
    for td in soup.find_all("td"):
        txt = td.get_text(strip=True)
        if "Share Holding" in txt and "as on" in txt.lower():
            row = td.find_parent("tr")
            if row:
                share_rows.append(row)

    out: list[dict[str, Any]] = []
    for row in share_rows:
        tds = row.find_all("td")
        rec: dict[str, Any] = {
            "shareholding_date": None,
            "sponsor_pct": None, "govt_pct": None,
            "institution_pct": None, "foreign_pct": None, "public_pct": None,
        }
        # Date from first td
        date_m = re.search(r"as on\s+(.+?)(?:\s*\(|$|\])", tds[0].get_text(strip=True), re.IGNORECASE)
        if date_m:
            rec["shareholding_date"] = _parse_as_on_date(date_m.group(1).strip())

        # Values from tds[2:] — each td text like "Sponsor/Director:30.37"
        for td in tds[2:]:
            text = td.get_text(strip=True)
            if ":" in text:
                parts = text.split(":", 1)
                key = parts[0].strip().lower()
                val = parts[1].strip().split()[0] if parts[1].strip() else ""
                if key in _label_map and val:
                    rec[_label_map[key]] = to_decimal(val)
        out.append(rec)
    return out


def _parse_right_issues(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Parse right-issue history from the 'Right Issue' th/td pair.

    Input cell text example: '1R:1 2010, 1R:1 2004,1R:2  2003'
    Each match yields {'year': int, 'ratio': str} where ratio is 'N:D'
    normalised (numerator:denominator, e.g. '1:1', '1:2').
    Returns an empty list if the cell is absent or empty.
    """
    raw = ""
    for row in soup.find_all("tr"):
        ths = row.find_all("th")
        for th in ths:
            if th.get_text(strip=True).lower() == "right issue":
                tds = row.find_all("td")
                if tds:
                    raw = tds[0].get_text(strip=True)
                break
        if raw:
            break

    out: list[dict[str, Any]] = []
    for m in _RIGHT_RE.finditer(raw):
        numerator, denominator, year = m.group(1), m.group(2), int(m.group(3))
        out.append({"year": year, "ratio": f"{numerator}:{denominator}"})
    return out


def _parse_status_table(soup: BeautifulSoup) -> dict[str, Any]:
    """Parse the 'Present Operational Status / Loan Status' table.

    Returns:
        status        – e.g. 'Active'
        short_loan_mn – Decimal or None
        long_loan_mn  – Decimal or None
        loan_as_on    – date | None  (from 'Present Loan Status as on ...' header row)
    """
    result: dict[str, Any] = {
        "status": None,
        "short_loan_mn": None,
        "long_loan_mn": None,
        "loan_as_on": None,
    }
    for tbl in soup.find_all("table"):
        text = tbl.get_text(" ", strip=True).lower()
        if "operational status" not in text:
            continue
        rows = tbl.find_all("tr")
        for row in rows:
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if not cells:
                continue
            first = cells[0].lower()
            # Status row: ['Present Operational Status', 'Active']
            if "operational status" in first and len(cells) >= 2:
                result["status"] = cells[-1].strip() or None
            # Loan header row: ['Present Loan Status as on December 31, 2025']
            elif "loan status" in first and "as on" in first:
                result["loan_as_on"] = _parse_as_on_date(cells[0])
            # Short-term loan row: ['', 'Short-term loan (mn)', '0']
            elif "short-term loan" in first or (len(cells) >= 3 and "short-term loan" in cells[1].lower()):
                val_str = cells[-1].replace(",", "").strip()
                if val_str not in ("-", "", "N/A"):
                    result["short_loan_mn"] = to_decimal(val_str)
            # Long-term loan row: ['Long-term loan (mn)', '11080']
            elif "long-term loan" in first:
                val_str = cells[-1].replace(",", "").strip()
                if val_str not in ("-", "", "N/A"):
                    result["long_loan_mn"] = to_decimal(val_str)
        break  # found the table
    return result


def _parse_links(soup: BeautifulSoup) -> dict[str, str | None]:
    """Extract 'Details of Financial Statement' (IR) and 'Price Sensitive Information' (PSI) links.

    DSE page has th/td rows:
      <tr><th>Details of Financial Statement</th><td><a href="...">...</a></td></tr>
      <tr><td>Price Sensitive Information</td><td><a href="...">...</a></td></tr>

    Returns {'ir_url': ..., 'psi_url': ...}.  Missing entries are None.
    """
    result: dict[str, str | None] = {"ir_url": None, "psi_url": None}
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        label = cells[0].get_text(strip=True).lower()
        # Get href from first <a> in the second cell, fall back to text
        link_cell = cells[1]
        a_tag = link_cell.find("a", href=True)
        url = (a_tag["href"] if a_tag else link_cell.get_text(strip=True)).strip()
        if not url:
            continue
        if "financial statement" in label or "investor" in label:
            result["ir_url"] = url
        elif "price sensitive" in label:
            result["psi_url"] = url
    return result


def _parse_quarterly_eps_full(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Parse the full quarterly EPS table, returning one dict per fiscal-year block.

    The table structure (confirmed CITYBANK 2026-06):
      row[0]: ['Particulars', 'Unaudited / Audited']     ← header label
      row[1]: ['Q1', 'Q2', 'Half Yearly', 'Q3', '9 Months', 'Annual']  ← column names
      row[2]: ['Ending on', 'Ending on', '6 Months', 'Ending on', 'Ending on']
      row[3]: ['202603', '', '', '']                      ← 6-digit fy tag (YYYYMM)
      row[4]: ['Earnings Per Share (EPS)']                ← section header
      row[5]: ['Basic', '1.580', '-', '-', '-', '-', '-']
      row[6]: ['Diluted*', ...]
      row[7]: ['Earnings Per Share (EPS) - continuing operations']
      row[8]: ['Basic', ...]
      row[9]: ['Diluted*', ...]
      row[10]:['Market price per share at period end', '29.7', ...]

    Column layout after the row-label cell:
      idx 1=Q1, 2=Q2, 3=Half Yearly, 4=Q3, 5=9 Months, 6=Annual

    Returns [{
        fy_tag, eps_basic, eps_q2, eps_half, eps_q3, eps_9m, eps_annual,
        period_end_price
    }, ...] — one entry per fy_tag found.
    """
    _FY_TAG_RE = re.compile(r"^\d{6}$")

    def _val(cells: list[str], idx: int) -> Any:
        v = cells[idx].strip() if idx < len(cells) else "-"
        return to_decimal(v) if v not in ("-", "", "N/A") else None

    out: list[dict[str, Any]] = []
    for tbl in soup.find_all("table"):
        headers_text = " ".join(c.get_text(strip=True) for c in tbl.find_all(["th", "td"])[:10]).lower()
        if "q1" not in headers_text or "half yearly" not in headers_text:
            continue

        all_rows = tbl.find_all("tr")
        # Walk through rows collecting fy_tag and EPS/price rows
        fy_tag: str | None = None
        eps_basic_row: list[str] | None = None
        price_row: list[str] | None = None
        in_eps_section = False

        for row in all_rows:
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if not cells:
                continue
            first = cells[0].strip()

            # fy tag row: first cell matches 6-digit pattern
            if _FY_TAG_RE.match(first):
                # Start a fresh block if we encounter another fy_tag
                if fy_tag is not None and eps_basic_row is not None:
                    out.append(_build_quarterly_row(fy_tag, eps_basic_row, price_row))
                fy_tag = first
                eps_basic_row = None
                price_row = None
                in_eps_section = False

            elif "earnings per share" in first.lower() and "continuing" not in first.lower():
                in_eps_section = True

            elif in_eps_section and first.lower() == "basic" and eps_basic_row is None:
                eps_basic_row = cells

            elif "market price per share" in first.lower():
                price_row = cells

        # Flush last block
        if fy_tag is not None and eps_basic_row is not None:
            out.append(_build_quarterly_row(fy_tag, eps_basic_row, price_row))

        break  # only one quarterly table expected
    return out


def _build_quarterly_row(
    fy_tag: str,
    eps_row: list[str],
    price_row: list[str] | None,
) -> dict[str, Any]:
    """Build a quarterly result dict from parsed row cells.

    eps_row[0] == 'Basic'; columns 1-6 are Q1, Q2, Half, Q3, 9M, Annual.
    """
    def _v(cells: list[str], idx: int) -> Any:
        v = cells[idx].strip() if idx < len(cells) else "-"
        return to_decimal(v) if v not in ("-", "", "N/A") else None

    return {
        "fy_tag":           fy_tag,
        "eps_basic":        _v(eps_row, 1),
        "eps_q2":           _v(eps_row, 2),
        "eps_half":         _v(eps_row, 3),
        "eps_q3":           _v(eps_row, 4),
        "eps_9m":           _v(eps_row, 5),
        "eps_annual":       _v(eps_row, 6),
        "period_end_price": _v(price_row, 1) if price_row else None,
    }


def _parse_full_name(soup: BeautifulSoup, ticker: str) -> str | None:
    """Extract company full name from page heading."""
    # DSE page has company name in <h2 class="BodyHead">
    for tag in soup.find_all("h2", class_="BodyHead"):
        text = tag.get_text(strip=True)
        if text and len(text) > 3 and ticker.upper() not in text.upper():
            if text.lower().startswith("company name:"):
                text = text.split(":", 1)[1].strip()
            return text
    # Fallback: first <h2> that isn't a section header
    for tag in soup.find_all(["h1", "h2"]):
        text = tag.get_text(strip=True)
        if text and len(text) > 5 and text not in ("Market Highlights",) and not text.startswith("DSE"):
            # Strip "Company Name:" prefix if present
            if text.lower().startswith("company name:"):
                text = text.split(":", 1)[1].strip()
            return text
    return None


def _parse_html(html: str, ticker: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    th_td   = _parse_th_td(soup)
    td_td   = _parse_td_td(soup)
    eps_nav = _parse_eps_nav_table(soup)
    pe_div  = _parse_pe_dividend_table(soup)
    q_eps   = _parse_quarterly_eps(soup)
    shares  = _parse_shareholding(soup)

    def _strip_commas(v: str) -> str:
        return v.replace(",", "").strip() if v else ""

    return {
        "ticker":          normalize_ticker(ticker),
        "full_name":       _parse_full_name(soup, ticker),
        "sector":          th_td.get("sector"),
        "listing_year":    int(td_td["listing_year"]) if td_td.get("listing_year", "").isdigit() else None,
        "market_category": td_td.get("market_category"),
        "fiscal_year_end": th_td.get("fiscal_year_end"),
        "authorized_cap_mn":  to_decimal(_strip_commas(th_td.get("authorized_cap_mn", ""))),
        "paid_up_cap_mn":     to_decimal(_strip_commas(th_td.get("paid_up_cap_mn", ""))),
        "total_securities":   pd.to_numeric(_strip_commas(th_td.get("total_securities", "")), errors="coerce"),
        "reserve_surplus_mn": to_decimal(_strip_commas(th_td.get("reserve_surplus_mn", ""))),
        "market_cap_mn":      to_decimal(_strip_commas(th_td.get("market_cap_mn", ""))),
        "free_float":         to_decimal(_strip_commas(th_td.get("free_float_cap_mn", ""))),
        "eps":                eps_nav["eps"],
        "nav":                eps_nav["nav"],
        "eps_q1":             q_eps["eps_q1"],
        "eps_q2":             q_eps["eps_q2"],
        "eps_q3":             q_eps["eps_q3"],
        "eps_q4":             q_eps["eps_q4"],
        "pe_audited":         pe_div["pe_audited"],
        "pe_unaudited":       None,  # not separately available
        "nav_price_ratio":    None,
        "dividend_yield":     pe_div["dividend_yield"],
        "shareholding_date":  shares["shareholding_date"],
        "sponsor_pct":        shares["sponsor_pct"],
        "govt_pct":           shares["govt_pct"],
        "institution_pct":    shares["institution_pct"],
        "foreign_pct":        shares["foreign_pct"],
        "public_pct":         shares["public_pct"],
        # 2nd/3rd shareholding periods — not on this page
        "shareholding_date_1": None, "sponsor_pct_1": None, "institution_pct_1": None,
        "foreign_pct_1": None, "public_pct_1": None,
        "shareholding_date_2": None, "sponsor_pct_2": None, "institution_pct_2": None,
        "foreign_pct_2": None, "public_pct_2": None,
        "address":   td_td.get("address"),
        "email":     td_td.get("email"),
        "web":       td_td.get("web"),
        "last_agm":  None,
        "short_loan_mn": None, "long_loan_mn": None,
        "rating":    None,
        "ma10": None, "ma20": None, "ma50": None, "ma100": None, "ma200": None,
        "ema10": None, "ema50": None, "beta": None,
        "news1_date": None, "news1_title": None,
        "news2_date": None, "news2_title": None,
        "news3_date": None, "news3_title": None,
    }


class DSEDirectCompanyInfoAdapter(BaseAdapter):
    """
    DSE official site — company fundamentals from /displayCompany.php.
    URL: https://www.dsebd.org/displayCompany.php?name={ticker}
    Method: HTTP + BeautifulSoup (no JS). Confirmed working 2026-05-23.
    Priority 3 — fallback when AmarStock AND bdshare both fail.
    Provides: sector, EPS (annual), NAV, P/E, dividend yield, shareholding,
              capital structure, quarterly EPS (when reported).
    Omits: MA signals, multi-period shareholding, news, beta, rating.
    """
    name = "dse_direct_company_info"
    priority = 3
    timeout_seconds = 20

    def normalize(self, raw: dict[str, Any]) -> pd.DataFrame:
        row = dict(raw)
        row["fetched_at"] = datetime.now(timezone.utc)
        row["source"] = self.name
        return pd.DataFrame([row])

    async def fetch(self, ticker: str = "", **kwargs: Any) -> AdapterResult:
        if not ticker:
            raise AdapterError(self.name, "ticker required", retryable=False)

        url = COMPANY_URL.format(ticker=ticker.upper())
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, headers=HEADERS, follow_redirects=True) as c:
                resp = await c.get(url)
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AdapterError(self.name, f"HTTP {exc.response.status_code} for {ticker}", retryable=True) from exc
        except Exception as exc:
            raise AdapterError(self.name, f"request failed: {exc}", retryable=True) from exc

        try:
            parsed = _parse_html(resp.text, ticker)
        except Exception as exc:
            raise AdapterError(self.name, f"parse failed: {exc}", retryable=False) from exc

        normalized = self.normalize(parsed)
        return AdapterResult(
            data=normalized,
            source_name=self.name,
            fetched_at=datetime.now(timezone.utc),
            quality="partial",  # no MA signals / multi-period shareholding
            records=1,
            raw_sample=parsed,
        )

    async def fetch_historical(self, ticker: str = "", **kwargs: Any) -> AdapterResult:
        """
        Multi-year fundamentals from displayCompany.php.
        Returns one DataFrame row per fiscal year (typically 5–30 years including
        dividend-only historical years).
        Columns: ticker, fiscal_year, eps, eps_basis, eps_diluted, nav, net_profit_mn,
                 tci_mn, pe, dividend_yield, cash_div_pct, stock_div_pct, source
        """
        if not ticker:
            raise AdapterError(self.name, "ticker required", retryable=False)

        url = COMPANY_URL.format(ticker=ticker.upper())
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, headers=HEADERS, follow_redirects=True) as c:
                resp = await c.get(url)
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AdapterError(self.name, f"HTTP {exc.response.status_code} for {ticker}", retryable=True) from exc
        except Exception as exc:
            raise AdapterError(self.name, f"request failed: {exc}", retryable=True) from exc

        try:
            from bs4 import BeautifulSoup as _BS
            soup        = _BS(resp.text, "lxml")
            eps_rows    = _parse_eps_nav_all_years(soup)
            pe_rows     = _parse_pe_dividend_all_years(soup)
            th_td       = _parse_th_td(soup)
            div_by_year = _parse_dividend_history_th_td(
                th_td.get("dividend_raw"), th_td.get("bonus_raw")
            )
            yearly      = _merge_yearly_rows(eps_rows, pe_rows, div_by_year)
        except Exception as exc:
            raise AdapterError(self.name, f"parse failed: {exc}", retryable=False) from exc

        if not yearly:
            raise AdapterError(
                self.name,
                f"no historical year rows found for {ticker}",
                retryable=False,
            )

        now = datetime.now(timezone.utc)
        rows = [
            {
                "ticker":        normalize_ticker(ticker),
                "fiscal_year":   y["fiscal_year"],
                "eps":           y["eps"],
                "eps_basis":     y["eps_basis"],
                "eps_diluted":   y["eps_diluted"],
                "nav":           y["nav"],
                "net_profit_mn": y["net_profit_mn"],
                "tci_mn":        y["tci_mn"],
                "pe":            y["pe"],
                "dividend_yield": y["dividend_yield"],
                "cash_div_pct":  y["cash_div_pct"],
                "stock_div_pct": y["stock_div_pct"],
                "fetched_at":    now,
                "source":        self.name,
            }
            for y in yearly
        ]
        df = pd.DataFrame(rows)
        return AdapterResult(
            data=df,
            source_name=self.name,
            fetched_at=now,
            quality="ok",
            records=len(df),
            raw_sample=yearly[0] if yearly else {},
        )

    async def health_check(self) -> bool:
        try:
            result = await self.fetch(ticker="GP")
            r = result.data.iloc[0]
            return r["ticker"] == "GP" and r["eps"] is not None
        except Exception:
            return False
