# Fundamentals Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture the full analyst-grade dataset from `dsebd.org/displayCompany.php` — net profit, shareholding trend, corporate actions, loans/ratings, quarterly EPS — fixing three parser bugs along the way.

**Architecture:** Header-grid table parsing replaces hardcoded column indices in `DSEDirectCompanyInfoAdapter`. A new `fetch_company_bundle()` returns five datasets from one HTTP request; the bulk loader writes all five with idempotent upserts. Migration 036 adds three tables and column extensions.

**Tech Stack:** Python 3.12, httpx, BeautifulSoup/lxml, pandas, asyncpg, PostgreSQL/TimescaleDB, pytest (smoke→pickle-fixture→unit convention).

**Spec:** `docs/superpowers/specs/2026-06-12-fundamentals-enrichment-design.md` (defects D1–D3, FR1–FR8, phases P1–P3).

**Environment notes:**
- Run Python via Docker (user instruction): `docker exec dse_worker python -m pytest …` or `docker exec dse_worker pytest …`. The repo is mounted at `/app` inside `dse_worker`.
- DB migration: `make migrate` (runs `db/migrate.py` in docker).
- Fixtures live in `tests/fixtures/` and are committed; unit tests run offline against them.

---

### Task 1: Smoke test — capture company-page HTML fixtures

**Files:**
- Create: `tests/smoke/test_dse_company_smoke.py`

The four tickers cover structurally distinct layouts: CITYBANK (bank, EPS-CO columns, 3 shareholding rows, rights history), GP (MNC/telecom, simple), SQURPHARMA (pharma, Jun year-end), FAMILYTEX (Z-category, sparse).

- [ ] **Step 1: Write the smoke test**

```python
"""
DSE company page smoke test — fetches displayCompany.php for structurally
distinct tickers and saves raw HTML pickles for offline unit tests.

Run: docker exec dse_worker python -m pytest tests/smoke/test_dse_company_smoke.py -v -s --no-cov
"""
from __future__ import annotations

import pickle
from pathlib import Path

import httpx
import pytest

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
FIXTURE_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Referer": "https://www.dsebd.org/",
}
URL = "https://www.dsebd.org/displayCompany.php?name={ticker}"

TICKERS = ["CITYBANK", "GP", "SQURPHARMA", "FAMILYTEX"]


@pytest.mark.parametrize("ticker", TICKERS)
async def test_company_page_fetch_and_save(ticker: str) -> None:
    async with httpx.AsyncClient(timeout=30, headers=HEADERS, follow_redirects=True) as c:
        resp = await c.get(URL.format(ticker=ticker))

    print(f"\n{ticker}: HTTP {resp.status_code} len={len(resp.text)}")
    assert resp.status_code == 200
    assert len(resp.text) > 50_000, "page suspiciously small"
    assert "Share Holding Percentage" in resp.text

    path = FIXTURE_DIR / f"dse_company_{ticker}.pkl"
    with open(path, "wb") as f:
        pickle.dump(resp.text, f)
    print(f"[SAVED] {path.name}")
```

- [ ] **Step 2: Run it to generate fixtures**

Run: `docker exec dse_worker python -m pytest tests/smoke/test_dse_company_smoke.py -v -s --no-cov`
Expected: 4 PASS, four `tests/fixtures/dse_company_*.pkl` files created on the host (volume mount).

- [ ] **Step 3: Commit (test + fixtures)**

```bash
git add tests/smoke/test_dse_company_smoke.py tests/fixtures/dse_company_*.pkl
git commit -m "test(smoke): capture displayCompany HTML fixtures for 4 layout-distinct tickers"
```

---

### Task 2: Migration 036

**Files:**
- Create: `db/migrations/036_fundamentals_enrichment.sql`

- [ ] **Step 1: Write the migration** — copy the SQL verbatim from design doc §3.4 (`036_fundamentals_enrichment.sql` block: fundamentals ALTER, `shareholding_history`, `corporate_actions`, `fundamentals_quarterly`, companies ALTER). The design doc is the source of truth; do not retype from memory.

- [ ] **Step 2: Apply**

Run: `make migrate`
Expected: log line applying `036_fundamentals_enrichment.sql`, exit 0.

- [ ] **Step 3: Verify idempotency** — run `make migrate` again.
Expected: 036 skipped (already in `_migrations`), exit 0.

- [ ] **Step 4: Commit**

```bash
git add db/migrations/036_fundamentals_enrichment.sql
git commit -m "feat(db): migration 036 — shareholding_history, corporate_actions, fundamentals_quarterly + column adds"
```

---

### Task 3: Header-grid expansion helper

**Files:**
- Modify: `extraction/adapters/dse_direct/company_info.py` (add after `_parse_td_td`, ~line 107)
- Create: `tests/unit/test_dse_company_info.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Offline unit tests for DSE displayCompany parsing — fixtures from test_dse_company_smoke.py."""
from __future__ import annotations

import pickle
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

FIXTURES = Path(__file__).parent.parent / "fixtures"

_CITYBANK = FIXTURES / "dse_company_CITYBANK.pkl"
pytestmark = pytest.mark.skipif(not _CITYBANK.exists(), reason="run tests/smoke/test_dse_company_smoke.py first")


def _soup(ticker: str) -> BeautifulSoup:
    html = pickle.loads((FIXTURES / f"dse_company_{ticker}.pkl").read_bytes())
    return BeautifulSoup(html, "lxml")


@pytest.fixture(scope="module")
def citybank() -> BeautifulSoup:
    return _soup("CITYBANK")


class TestExpandHeaderGrid:
    def test_eps_nav_table_labels(self, citybank):
        from extraction.adapters.dse_direct.company_info import _expand_header_grid, _find_eps_nav_table

        tbl = _find_eps_nav_table(citybank)
        labels = _expand_header_grid(tbl)

        assert labels, "no header labels extracted"
        joined = " | ".join(labels)
        assert "profit for the year" in joined
        assert "nav per share" in joined
        # every label is lowercase, hierarchical parts joined with ' > '
        assert all(lab == lab.lower() for lab in labels)

    def test_grid_handles_all_fixture_layouts(self):
        from extraction.adapters.dse_direct.company_info import _expand_header_grid, _find_eps_nav_table

        for ticker in ("CITYBANK", "GP", "SQURPHARMA", "FAMILYTEX"):
            tbl = _find_eps_nav_table(_soup(ticker))
            if tbl is None:  # Z-cat may lack the table entirely
                continue
            labels = _expand_header_grid(tbl)
            assert any("nav per share" in lab for lab in labels), ticker
```

- [ ] **Step 2: Run to verify failure**

Run: `docker exec dse_worker python -m pytest tests/unit/test_dse_company_info.py -v --no-cov`
Expected: FAIL — `ImportError: cannot import name '_expand_header_grid'`

- [ ] **Step 3: Implement**

Add to `company_info.py`:

```python
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


def _find_col(labels: list[str], *needle_sets: tuple[str, ...]) -> int | None:
    """Index of the first label containing ALL needles; needle sets tried in
    preference order. None when nothing matches."""
    for needles in needle_sets:
        for i, lab in enumerate(labels):
            if all(n in lab for n in needles):
                return i
    return None
```

- [ ] **Step 4: Run tests**

Run: `docker exec dse_worker python -m pytest tests/unit/test_dse_company_info.py -v --no-cov`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add extraction/adapters/dse_direct/company_info.py tests/unit/test_dse_company_info.py
git commit -m "feat(dse): header-grid expansion for colspan/rowspan company tables"
```

---

### Task 4: Rewrite `_parse_eps_nav_all_years` — profit, TCI, eps_basis (fixes D1, D3)

**Files:**
- Modify: `extraction/adapters/dse_direct/company_info.py:209-234` (replace function)
- Test: `tests/unit/test_dse_company_info.py`

- [ ] **Step 1: Write the failing tests**

```python
class TestEpsNavAllYears:
    def test_citybank_profit_and_nav(self, citybank):
        from extraction.adapters.dse_direct.company_info import _parse_eps_nav_all_years

        rows = {r["fiscal_year"]: r for r in _parse_eps_nav_all_years(citybank)}

        assert 2025 in rows and 2021 in rows
        assert float(rows[2025]["net_profit_mn"]) == pytest.approx(13242.27)
        assert float(rows[2021]["net_profit_mn"]) == pytest.approx(5494.16)
        assert float(rows[2025]["nav"]) == pytest.approx(40.67)
        assert float(rows[2025]["eps"]) == pytest.approx(8.71)
        assert rows[2025]["eps_basis"] is not None

    def test_d3_no_basic_fallthrough_for_diluted(self, citybank):
        """Page shows '-' for diluted EPS — must be None, never the basic value."""
        from extraction.adapters.dse_direct.company_info import _parse_eps_nav_all_years

        rows = {r["fiscal_year"]: r for r in _parse_eps_nav_all_years(citybank)}
        assert rows[2025]["eps_diluted"] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `docker exec dse_worker python -m pytest tests/unit/test_dse_company_info.py::TestEpsNavAllYears -v --no-cov`
Expected: FAIL (`net_profit_mn` KeyError on current implementation)

- [ ] **Step 3: Replace the function**

```python
def _parse_eps_nav_all_years(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """All year rows from the EPS+NAV table via header-grid column mapping.
    → [{fiscal_year, eps, eps_basis, eps_diluted, nav, net_profit_mn, tci_mn}, ...]"""
    tbl = _find_eps_nav_table(soup)
    if tbl is None:
        return []
    labels = _expand_header_grid(tbl)
    if not labels:
        raise ValueError("EPS/NAV table header grid unparseable — layout changed?")

    # (column, basis-tag) preference: restated over original, basic before diluted.
    # 'continuing operations' columns excluded by requiring 'earnings per share'.
    eps_candidates = [
        (("earnings per share", "basic", "restated"), "basic_restated"),
        (("earnings per share", "basic", "original"), "basic_original"),
        (("earnings per share", "diluted", "restated"), "diluted_restated"),
        (("earnings per share", "diluted", "original"), "diluted_original"),
    ]
    dil_col = _find_col(labels,
                        ("earnings per share", "diluted", "restated"),
                        ("earnings per share", "diluted", "original"))
    nav_col = _find_col(labels, ("nav per share", "restated"), ("nav per share",))
    profit_col = _find_col(labels, ("profit for the year",))
    tci_col = _find_col(labels, ("tci",))

    def _cell(cells: list[str], idx: int | None) -> Any:
        if idx is None or idx >= len(cells):
            return None
        v = cells[idx].replace(",", "").strip()
        return to_decimal(v) if v not in ("-", "", "N/A") else None

    rows_out: list[dict[str, Any]] = []
    for row in tbl.find_all("tr"):
        cells = [td.get_text(strip=True) for td in row.find_all("td")]
        if not (cells and re.match(r"^\d{4}$", cells[0])):
            continue
        eps_val, eps_basis = None, None
        for needles, basis in eps_candidates:
            v = _cell(cells, _find_col(labels, needles))
            if v is not None:
                eps_val, eps_basis = v, basis
                break
        rows_out.append({
            "fiscal_year":   int(cells[0]),
            "eps":           eps_val,
            "eps_basis":     eps_basis,
            "eps_diluted":   _cell(cells, dil_col),   # D3: no basic fallback
            "nav":           _cell(cells, nav_col),
            "net_profit_mn": _cell(cells, profit_col),  # D1 fix
            "tci_mn":        _cell(cells, tci_col),
        })
    return rows_out
```

Note: per-row candidate loop (not table-level) because banks publish restated values only for prior years — the latest year often has only "original".

- [ ] **Step 4: Run all unit tests in the file** (the old `[4,3,2,1]` callers are gone — `_merge_yearly_rows` consumers updated in Task 5; if `make test` shows other breakage from the changed return shape, fix the caller, not the parser).

Run: `docker exec dse_worker python -m pytest tests/unit/test_dse_company_info.py -v --no-cov`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add extraction/adapters/dse_direct/company_info.py tests/unit/test_dse_company_info.py
git commit -m "fix(dse): D1+D3 — capture net profit/TCI, stop diluted-EPS basic fallthrough"
```

---

### Task 5: P/E table via grid + `_merge_yearly_rows` dividend years (fixes D2)

**Files:**
- Modify: `extraction/adapters/dse_direct/company_info.py` — `_parse_pe_dividend_all_years` (~line 240), `_merge_yearly_rows` (~line 298)
- Test: `tests/unit/test_dse_company_info.py`

- [ ] **Step 1: Write the failing tests**

```python
class TestPeDividendAndMerge:
    def test_pe_and_yield_per_year(self, citybank):
        from extraction.adapters.dse_direct.company_info import _parse_pe_dividend_all_years

        rows = {r["fiscal_year"]: r for r in _parse_pe_dividend_all_years(citybank)}
        assert float(rows[2021]["pe"]) == pytest.approx(4.82)
        assert float(rows[2021]["dividend_yield"]) == pytest.approx(5.04)

    def test_d2_dividend_only_years_survive_merge(self, citybank):
        """CITYBANK bonus history reaches 2004 — merge must include years
        absent from the EPS table (which starts at 2021)."""
        from extraction.adapters.dse_direct.company_info import (
            _merge_yearly_rows,
            _parse_dividend_history_th_td,
            _parse_eps_nav_all_years,
            _parse_pe_dividend_all_years,
            _parse_th_td,
        )

        th_td = _parse_th_td(citybank)
        div = _parse_dividend_history_th_td(th_td.get("dividend_raw"), th_td.get("bonus_raw"))
        merged = _merge_yearly_rows(
            _parse_eps_nav_all_years(citybank),
            _parse_pe_dividend_all_years(citybank),
            div,
        )
        years = {r["fiscal_year"] for r in merged}
        assert 2004 in years and 2015 in years
        by_year = {r["fiscal_year"]: r for r in merged}
        assert float(by_year[2004]["stock_div_pct"]) == pytest.approx(50.0)
        assert float(by_year[2015]["cash_div_pct"]) == pytest.approx(22.0)
        assert by_year[2004]["eps"] is None and by_year[2004]["nav"] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `docker exec dse_worker python -m pytest tests/unit/test_dse_company_info.py::TestPeDividendAndMerge -v --no-cov`
Expected: FAIL (`dividend_yield` KeyError; 2004 missing from merge)

- [ ] **Step 3: Implement**

Replace `_parse_pe_dividend_all_years` body to use the grid (same `_cell` pattern as Task 4):

```python
def _parse_pe_dividend_all_years(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Year-end P/E + dividend-yield rows via header-grid mapping.
    → [{fiscal_year, pe, dividend_yield}, ...]"""
    target = None
    for tbl in soup.find_all("table"):
        head = " ".join(c.get_text(strip=True) for c in tbl.find_all(["th", "td"])[:20]).lower()
        if "dividend yield" in head and "year" in head:
            target = tbl
            break
    if target is None:
        return []
    labels = _expand_header_grid(target)
    pe_col = _find_col(labels,
                       ("p/e", "basic", "restated"), ("p/e", "basic", "original"),
                       ("p/e", "diluted", "restated"), ("p/e", "diluted", "original"))
    yield_col = _find_col(labels, ("dividend yield",))

    def _cell(cells: list[str], idx: int | None) -> Any:
        if idx is None or idx >= len(cells):
            return None
        v = cells[idx].replace(",", "").strip()
        return to_decimal(v) if v not in ("-", "", "N/A") else None

    rows_out = []
    for row in target.find_all("tr"):
        cells = [td.get_text(strip=True) for td in row.find_all("td")]
        if not (cells and re.match(r"^\d{4}$", cells[0])):
            continue
        rows_out.append({
            "fiscal_year":    int(cells[0]),
            "pe":             _cell(cells, pe_col),
            "dividend_yield": _cell(cells, yield_col),
        })
    return rows_out
```

In `_merge_yearly_rows`, change the year-set line and carry the new fields:

```python
    years = sorted(set(eps_map) | set(pe_map) | set(div_map))  # D2: dividend-only years kept
```

and extend the per-year dict:

```python
        out.append({
            "fiscal_year":    yr,
            "eps":            e.get("eps"),
            "eps_basis":      e.get("eps_basis"),
            "eps_diluted":    e.get("eps_diluted"),
            "nav":            e.get("nav"),
            "net_profit_mn":  e.get("net_profit_mn"),
            "tci_mn":         e.get("tci_mn"),
            "pe":             p.get("pe"),
            "dividend_yield": p.get("dividend_yield"),
            "cash_div_pct":   d.get("cash"),
            "stock_div_pct":  d.get("bonus"),
        })
```

(`div_map` already defaults to `{}`; move the `years` line below it.)

- [ ] **Step 4: Run tests**

Run: `docker exec dse_worker python -m pytest tests/unit/test_dse_company_info.py -v --no-cov`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add extraction/adapters/dse_direct/company_info.py tests/unit/test_dse_company_info.py
git commit -m "fix(dse): D2 — dividend-only years survive merge; per-year dividend yield"
```

---

### Task 6: Shareholding history, right issues, status, links, quarterly parsers

**Files:**
- Modify: `extraction/adapters/dse_direct/company_info.py` (new functions after `_parse_shareholding`)
- Test: `tests/unit/test_dse_company_info.py`

- [ ] **Step 1: Write the failing tests**

```python
from datetime import date


class TestNewParsers:
    def test_shareholding_all_three_periods(self, citybank):
        from extraction.adapters.dse_direct.company_info import _parse_shareholding_all

        rows = _parse_shareholding_all(citybank)
        assert len(rows) == 3
        by_date = {r["as_on_date"]: r for r in rows}
        assert date(2025, 12, 31) in by_date
        assert date(2026, 5, 31) in by_date
        assert float(by_date[date(2025, 12, 31)]["institution_pct"]) == pytest.approx(19.33)
        assert float(by_date[date(2026, 5, 31)]["institution_pct"]) == pytest.approx(15.00)
        assert float(by_date[date(2026, 5, 31)]["sponsor_pct"]) == pytest.approx(30.37)

    def test_right_issues(self):
        from extraction.adapters.dse_direct.company_info import _parse_right_issues

        rows = _parse_right_issues("1R:1 2010, 1R:1 2004,1R:2  2003")
        assert [(r["fiscal_year"], r["ratio_text"], r["ratio"]) for r in rows] == [
            (2010, "1R:1", 1.0), (2004, "1R:1", 1.0), (2003, "1R:2", 0.5),
        ]
        assert _parse_right_issues(None) == []
        assert _parse_right_issues("") == []

    def test_status_table(self, citybank):
        from extraction.adapters.dse_direct.company_info import _parse_status_table

        s = _parse_status_table(citybank)
        assert s["operational_status"] == "Active"
        assert float(s["short_loan_mn"]) == 0
        assert float(s["long_loan_mn"]) == 11080
        assert s["loan_as_on"] == date(2025, 12, 31)

    def test_links(self, citybank):
        from extraction.adapters.dse_direct.company_info import _parse_links

        links = _parse_links(citybank)
        assert links["ir_url"] == "https://www.citybankplc.com/investor-relation"
        assert links["psi_url"] == "https://www.citybankplc.com/price-sensitive-information"

    def test_quarterly_full(self, citybank):
        from extraction.adapters.dse_direct.company_info import _parse_quarterly_eps_full

        rows = _parse_quarterly_eps_full(citybank)
        q1 = next(r for r in rows if r["quarter"] == 1)
        assert q1["fiscal_year"] == 2026
        assert float(q1["eps_basic"]) == pytest.approx(1.580)
        assert float(q1["period_end_price"]) == pytest.approx(29.7)
        # Q4 derived from Annual - 9 Months; both '-' on this page → no Q4 row
        assert all(r["quarter"] != 4 for r in rows)
```

- [ ] **Step 2: Run to verify failure**

Run: `docker exec dse_worker python -m pytest tests/unit/test_dse_company_info.py::TestNewParsers -v --no-cov`
Expected: FAIL — ImportError for each new function

- [ ] **Step 3: Implement**

```python
_AS_ON_RE = re.compile(r"as on\s+([A-Za-z]{3,9})\s+(\d{1,2}),\s*(\d{4})", re.IGNORECASE)
_MONTHS = {m.lower(): i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _parse_as_on_date(text: str) -> Any:
    """'… as on Dec 31, 2025 (year ended)…' → date(2025, 12, 31), else None."""
    from datetime import date as _date
    m = _AS_ON_RE.search(text or "")
    if not m:
        return None
    mon = _MONTHS.get(m.group(1)[:3].lower())
    if not mon:
        return None
    return _date(int(m.group(3)), mon, int(m.group(2)))


def _parse_shareholding_all(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Every dated 'Share Holding Percentage [as on …]' row (up to 3 per page).
    → [{as_on_date, sponsor_pct, govt_pct, institution_pct, foreign_pct, public_pct}, ...]"""
    _label_map = {
        "sponsor/director": "sponsor_pct", "govt": "govt_pct", "government": "govt_pct",
        "institute": "institution_pct", "foreign": "foreign_pct", "public": "public_pct",
    }
    out: list[dict[str, Any]] = []
    seen: set[Any] = set()
    for td in soup.find_all("td"):
        txt = td.get_text(strip=True)
        if "Share Holding" not in txt or "as on" not in txt.lower():
            continue
        row = td.find_parent("tr")
        if row is None:
            continue
        as_on = _parse_as_on_date(txt)
        if as_on is None or as_on in seen:
            continue
        seen.add(as_on)
        entry: dict[str, Any] = {
            "as_on_date": as_on, "sponsor_pct": None, "govt_pct": None,
            "institution_pct": None, "foreign_pct": None, "public_pct": None,
        }
        for cell in row.find_all("td")[1:]:
            text = cell.get_text(strip=True)
            if ":" in text:
                key, _, val = text.partition(":")
                key = key.strip().lower()
                val = val.strip().split()[0] if val.strip() else ""
                if key in _label_map and val:
                    entry[_label_map[key]] = to_decimal(val)
        out.append(entry)
    return out


_RIGHT_RE = re.compile(r"(\d+)R:(\d+)\s*(\d{4})")


def _parse_right_issues(raw: str | None) -> list[dict[str, Any]]:
    """'1R:1 2010, 1R:2 2003' → [{fiscal_year, ratio_text, ratio}, ...];
    ratio = new shares per existing share (1R:2 = one new per two held = 0.5)."""
    out = []
    for m in _RIGHT_RE.finditer(raw or ""):
        new, base, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
        out.append({
            "fiscal_year": yr,
            "ratio_text": f"{new}R:{base}",
            "ratio": new / base if base else None,
        })
    return out


def _parse_status_table(soup: BeautifulSoup) -> dict[str, Any]:
    """Operational status / loans / credit rating / delisting table."""
    result: dict[str, Any] = {
        "operational_status": None, "short_loan_mn": None, "long_loan_mn": None,
        "loan_as_on": None, "credit_rating_st": None, "credit_rating_lt": None,
        "delisting_remark": None,
    }
    for row in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["th", "td"])]
        for i, cell in enumerate(cells):
            low = cell.lower()
            nxt = cells[i + 1].replace(",", "") if i + 1 < len(cells) else ""
            if "present operational status" in low and nxt:
                result["operational_status"] = cells[i + 1]
            elif "loan status as on" in low:
                result["loan_as_on"] = _parse_as_on_date("as on " + cell.split("as on", 1)[-1])
            elif low.startswith("short-term loan") and nxt:
                result["short_loan_mn"] = to_decimal(nxt)
            elif low.startswith("long-term loan") and nxt:
                result["long_loan_mn"] = to_decimal(nxt)
            elif "otc" in low and "delisting" in low and nxt:
                result["delisting_remark"] = cells[i + 1]
    return result


def _parse_links(soup: BeautifulSoup) -> dict[str, Any]:
    """IR + PSI URLs from the financial-statement links row."""
    result: dict[str, Any] = {"ir_url": None, "psi_url": None}
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        for i, cell in enumerate(cells):
            low = cell.get_text(strip=True).lower()
            if i + 1 >= len(cells):
                continue
            href_cell = cells[i + 1]
            link = href_cell.find("a")
            url = (link.get("href") if link else href_cell.get_text(strip=True)) or None
            if "details of financial statement" in low:
                result["ir_url"] = url
            elif "price sensitive information" in low:
                result["psi_url"] = url
    return result


def _parse_quarterly_eps_full(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Interim EPS rows with fiscal year and period-end price.

    Page columns: Q1 | Q2 | Half Yearly | Q3 | 9 Months | Annual.
    Q1–Q3 read directly; cumulative columns ignored; Q4 derived as
    Annual − 9 Months when both present (the Annual column is NOT Q4).
    Fiscal year from the 'Ending on … YYYYMM' header label."""
    for tbl in soup.find_all("table"):
        head = " ".join(c.get_text(strip=True) for c in tbl.find_all(["th", "td"])[:14]).lower()
        if not ("q1" in head and "q2" in head and "half yearly" in head):
            continue
        fy_m = re.search(r"(\d{4})(\d{2})", tbl.get_text(" ", strip=True))
        fiscal_year = int(fy_m.group(1)) if fy_m else None
        if fiscal_year is None:
            return []

        def _val(cells: list[str], idx: int) -> Any:
            v = cells[idx].strip() if idx < len(cells) else "-"
            return to_decimal(v) if v not in ("-", "", "N/A") else None

        basic: dict[str, Any] = {}
        diluted: dict[str, Any] = {}
        price: dict[str, Any] = {}
        in_eps = False
        for row in tbl.find_all("tr"):
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if not cells:
                continue
            label = cells[0].lower()
            if "earnings per share" in label and "continuing" not in label:
                in_eps = True
                continue
            if "continuing" in label or "market price" in label:
                in_eps = "market price" not in label and in_eps
                if "market price" in label:
                    price = {"q1": _val(cells, 1), "q2": _val(cells, 2),
                             "q3": _val(cells, 4), "nine_m": _val(cells, 5), "annual": _val(cells, 6)}
                continue
            if in_eps and label == "basic" and not basic:
                basic = {"q1": _val(cells, 1), "q2": _val(cells, 2), "q3": _val(cells, 4),
                         "nine_m": _val(cells, 5), "annual": _val(cells, 6)}
            elif in_eps and label.startswith("diluted") and not diluted:
                diluted = {"q1": _val(cells, 1), "q2": _val(cells, 2), "q3": _val(cells, 4),
                           "nine_m": _val(cells, 5), "annual": _val(cells, 6)}

        rows_out = []
        for q, key in ((1, "q1"), (2, "q2"), (3, "q3")):
            eb, ed = basic.get(key), diluted.get(key)
            if eb is None and ed is None:
                continue
            rows_out.append({
                "fiscal_year": fiscal_year, "quarter": q,
                "eps_basic": eb, "eps_diluted": ed,
                "period_end_price": price.get(key),
            })
        ann, nine = basic.get("annual"), basic.get("nine_m")
        if ann is not None and nine is not None:
            rows_out.append({
                "fiscal_year": fiscal_year, "quarter": 4,
                "eps_basic": float(ann) - float(nine), "eps_diluted": None,
                "period_end_price": price.get("annual"),
            })
        return rows_out
    return []
```

- [ ] **Step 4: Run tests**

Run: `docker exec dse_worker python -m pytest tests/unit/test_dse_company_info.py -v --no-cov`
Expected: all PASS. If `test_status_table` or `test_links` fails on cell-structure mismatch, print the relevant table rows from the fixture and adjust the label matching — the assertion values are ground truth from the live page (2026-06-12).

- [ ] **Step 5: Commit**

```bash
git add extraction/adapters/dse_direct/company_info.py tests/unit/test_dse_company_info.py
git commit -m "feat(dse): shareholding-history, right-issue, status, links, quarterly parsers"
```

---

### Task 7: `CompanyBundle` + `fetch_company_bundle()`

**Files:**
- Modify: `extraction/adapters/dse_direct/company_info.py` (dataclass + method on adapter; `fetch_historical` becomes a wrapper)
- Test: `tests/unit/test_dse_company_info.py`

- [ ] **Step 1: Write the failing test**

```python
class TestCompanyBundle:
    def test_bundle_from_fixture_html(self, citybank):
        import pandas as pd
        from extraction.adapters.dse_direct.company_info import (
            DSEDirectCompanyInfoAdapter,
            _build_bundle,
        )

        html = pickle.loads((FIXTURES / "dse_company_CITYBANK.pkl").read_bytes())
        bundle = _build_bundle(html, "CITYBANK", source=DSEDirectCompanyInfoAdapter.name)

        assert isinstance(bundle.yearly, pd.DataFrame) and len(bundle.yearly) >= 20
        assert "net_profit_mn" in bundle.yearly.columns
        assert len(bundle.shareholding) == 3
        assert len(bundle.quarterly) >= 1
        # actions: 11 cash + 19 bonus + 3 rights
        assert set(bundle.actions["action_type"]) == {"cash_div", "stock_div", "right_issue"}
        assert bundle.company_meta["scrip_code"] == "11102"
        assert float(bundle.company_meta["face_value"]) == pytest.approx(10.0)
        assert bundle.company_meta["market_lot"] == 1
        assert bundle.company_meta["ir_url"].endswith("investor-relation")
        assert (bundle.yearly["ticker"] == "CITYBANK").all()
```

- [ ] **Step 2: Run to verify failure**

Run: `docker exec dse_worker python -m pytest tests/unit/test_dse_company_info.py::TestCompanyBundle -v --no-cov`
Expected: FAIL — ImportError `_build_bundle`

- [ ] **Step 3: Implement**

Add `face/par value`, `market lot`, `debut trading date` to the `_MAP` in `_parse_th_td`:

```python
        "face/par value":                    "face_value",
        "market lot":                        "market_lot",
        "debut trading date":                "debut_trading_date",
```

Scrip code comes from the Trading Code header table — add a small helper:

```python
def _parse_scrip_code(soup: BeautifulSoup) -> str | None:
    for row in soup.find_all("tr"):
        cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
        for i, cell in enumerate(cells):
            if cell.lower().startswith("scrip code") and i + 1 < len(cells):
                return cells[i + 1] or None
    return None
```

Then the bundle (module level, plus pure builder so unit tests skip HTTP):

```python
from dataclasses import dataclass, field


@dataclass
class CompanyBundle:
    yearly: pd.DataFrame
    quarterly: pd.DataFrame
    shareholding: pd.DataFrame
    actions: pd.DataFrame
    company_meta: dict[str, Any] = field(default_factory=dict)


def _build_bundle(html: str, ticker: str, source: str) -> CompanyBundle:
    """Parse one displayCompany page into all five datasets (FR6: one fetch, all writes)."""
    soup = BeautifulSoup(html, "lxml")
    t = normalize_ticker(ticker)
    now = datetime.now(timezone.utc)

    th_td = _parse_th_td(soup)
    div_by_year = _parse_dividend_history_th_td(th_td.get("dividend_raw"), th_td.get("bonus_raw"))
    yearly_rows = _merge_yearly_rows(
        _parse_eps_nav_all_years(soup), _parse_pe_dividend_all_years(soup), div_by_year)
    for r in yearly_rows:
        r.update(ticker=t, fetched_at=now, source=source)

    quarterly = _parse_quarterly_eps_full(soup)
    for r in quarterly:
        r.update(ticker=t, fetched_at=now, source=source)

    shareholding = _parse_shareholding_all(soup)
    for r in shareholding:
        r.update(ticker=t, fetched_at=now, source=source)

    actions: list[dict[str, Any]] = []
    for yr, d in div_by_year.items():
        if d.get("cash") is not None:
            actions.append({"fiscal_year": yr, "action_type": "cash_div",
                            "value_pct": d["cash"], "ratio_text": None, "ratio": None})
        if d.get("bonus") is not None:
            actions.append({"fiscal_year": yr, "action_type": "stock_div",
                            "value_pct": d["bonus"], "ratio_text": None, "ratio": None})
    # Right Issue string is a th/td row — reuse the th/td scan with one extra key.
    right_raw = None
    for row in soup.find_all("tr"):
        cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
        for i, cell in enumerate(cells):
            if cell.lower().rstrip("*").strip() == "right issue" and i + 1 < len(cells):
                right_raw = cells[i + 1]
    for r in _parse_right_issues(right_raw):
        actions.append({**r, "action_type": "right_issue", "value_pct": None})
    for a in actions:
        a.update(ticker=t, source=source)

    status = _parse_status_table(soup)
    links = _parse_links(soup)
    td_td = _parse_td_td(soup)
    meta = {
        "ticker": t,
        "face_value": to_decimal(th_td.get("face_value", "")) if th_td.get("face_value") else None,
        "market_lot": int(th_td["market_lot"]) if th_td.get("market_lot", "").isdigit() else None,
        "debut_trading_date": th_td.get("debut_trading_date") or None,
        "scrip_code": _parse_scrip_code(soup),
        "electronic_share": (td_td.get("electronic_share") == "Y") if "electronic_share" in td_td else None,
        **status, **links,
    }

    return CompanyBundle(
        yearly=pd.DataFrame(yearly_rows),
        quarterly=pd.DataFrame(quarterly),
        shareholding=pd.DataFrame(shareholding),
        actions=pd.DataFrame(actions),
        company_meta=meta,
    )
```

(`electronic_share` requires adding `"electronic share": "electronic_share"` to the `_MAP` in `_parse_td_td`.)

On the adapter:

```python
    async def fetch_company_bundle(self, ticker: str = "", **kwargs: Any) -> CompanyBundle:
        """One GET → yearly/quarterly/shareholding/actions/company_meta."""
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
            bundle = _build_bundle(resp.text, ticker, source=self.name)
        except Exception as exc:
            raise AdapterError(self.name, f"parse failed: {exc}", retryable=False) from exc
        if bundle.yearly.empty:
            raise AdapterError(self.name, f"no historical year rows found for {ticker}", retryable=False)
        return bundle
```

Rewrite `fetch_historical` as a thin back-compat wrapper (keep its docstring and `AdapterResult` shape):

```python
    async def fetch_historical(self, ticker: str = "", **kwargs: Any) -> AdapterResult:
        bundle = await self.fetch_company_bundle(ticker=ticker)
        return AdapterResult(
            data=bundle.yearly,
            source_name=self.name,
            fetched_at=datetime.now(timezone.utc),
            quality="ok",
            records=len(bundle.yearly),
            raw_sample=bundle.yearly.iloc[0].to_dict() if len(bundle.yearly) else {},
        )
```

- [ ] **Step 4: Run tests**

Run: `docker exec dse_worker python -m pytest tests/unit/test_dse_company_info.py -v --no-cov`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add extraction/adapters/dse_direct/company_info.py tests/unit/test_dse_company_info.py
git commit -m "feat(dse): fetch_company_bundle — one request feeds all five datasets"
```

---

### Task 8: Loader writes all five datasets

**Files:**
- Modify: `extraction/bulk_load/fundamentals_historical_loader.py` (rewrite `_load_one`)
- Create: `tests/unit/test_fundamentals_loader.py`

- [ ] **Step 1: Write the failing tests** (fake pool capturing SQL + args)

```python
"""Unit tests for the fundamentals bulk loader write paths (fake pool, no DB)."""
from __future__ import annotations

import pickle
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures"
_CITYBANK = FIXTURES / "dse_company_CITYBANK.pkl"
pytestmark = pytest.mark.skipif(not _CITYBANK.exists(), reason="run smoke first")


class FakePool:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, *args):
        self.calls.append((sql, args))
        return "INSERT 0 1"


@pytest.fixture()
def bundle():
    from extraction.adapters.dse_direct.company_info import DSEDirectCompanyInfoAdapter, _build_bundle
    html = pickle.loads(_CITYBANK.read_bytes())
    return _build_bundle(html, "CITYBANK", source=DSEDirectCompanyInfoAdapter.name)


async def test_write_bundle_touches_all_tables(bundle):
    from extraction.bulk_load.fundamentals_historical_loader import _write_bundle

    pool = FakePool()
    counts = await _write_bundle(pool, bundle, job_id="test_job")

    sqls = " ".join(sql for sql, _ in pool.calls)
    for table in ("fundamentals", "fundamentals_quarterly", "shareholding_history",
                  "corporate_actions", "companies"):
        assert table in sqls, f"no write against {table}"
    assert counts["fundamentals"] >= 20      # 2004–2025 dividend years included
    assert counts["shareholding"] == 3
    assert counts["actions"] >= 30
    assert counts["quarterly"] >= 1


async def test_net_profit_converted_mn_to_bdt(bundle):
    from extraction.bulk_load.fundamentals_historical_loader import _write_bundle

    pool = FakePool()
    await _write_bundle(pool, bundle, job_id="test_job")

    fund_calls = [args for sql, args in pool.calls if "INSERT INTO fundamentals\n" in sql or "INSERT INTO fundamentals " in sql]
    # 2025 row: profit 13242.27 mn → 13_242_270_000 BDT
    profits = [a for call in fund_calls for a in call if isinstance(a, float) and a > 1e12]
    assert not profits, "value too large — double conversion?"
    assert any(
        a is not None and abs(float(a) - 13_242_270_000) < 1 for call in fund_calls for a in call if a is not None and isinstance(a, (int, float))
    )


async def test_idempotent_rerun_same_counts(bundle):
    from extraction.bulk_load.fundamentals_historical_loader import _write_bundle

    pool = FakePool()
    c1 = await _write_bundle(pool, bundle, job_id="j1")
    c2 = await _write_bundle(pool, bundle, job_id="j2")
    assert c1 == c2
    # every INSERT carries ON CONFLICT — no bare inserts
    for sql, _ in pool.calls:
        if sql.strip().startswith("INSERT"):
            assert "ON CONFLICT" in sql, sql[:80]
```

- [ ] **Step 2: Run to verify failure**

Run: `docker exec dse_worker python -m pytest tests/unit/test_fundamentals_loader.py -v --no-cov`
Expected: FAIL — ImportError `_write_bundle`

- [ ] **Step 3: Implement `_write_bundle` and rewire `_load_one`**

```python
async def _write_bundle(pool, bundle, job_id: str) -> dict[str, int]:
    """Write all five bundle datasets. Returns per-table row counts."""
    counts = {"fundamentals": 0, "quarterly": 0, "shareholding": 0, "actions": 0, "companies": 0}

    for _, row in bundle.yearly.iterrows():
        profit_mn = _num(row.get("net_profit_mn"))
        tci_mn = _num(row.get("tci_mn"))
        await pool.execute(
            """
            INSERT INTO fundamentals
                (ticker, fiscal_year, eps, eps_basis, eps_diluted, nav, pe,
                 net_profit_bdt, total_comprehensive_income_bdt, dividend_yield_pct,
                 cash_div_pct, stock_div_pct, fetched_at, source, ingestion_job)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
            ON CONFLICT (ticker, fiscal_year)
            WHERE fiscal_year IS NOT NULL
            DO UPDATE SET
                eps = EXCLUDED.eps, eps_basis = EXCLUDED.eps_basis,
                eps_diluted = EXCLUDED.eps_diluted, nav = EXCLUDED.nav,
                pe = EXCLUDED.pe, net_profit_bdt = EXCLUDED.net_profit_bdt,
                total_comprehensive_income_bdt = EXCLUDED.total_comprehensive_income_bdt,
                dividend_yield_pct = EXCLUDED.dividend_yield_pct,
                cash_div_pct = EXCLUDED.cash_div_pct, stock_div_pct = EXCLUDED.stock_div_pct,
                fetched_at = EXCLUDED.fetched_at, ingestion_job = EXCLUDED.ingestion_job
            """,
            row["ticker"], int(row["fiscal_year"]),
            _num(row.get("eps")), row.get("eps_basis"), _num(row.get("eps_diluted")),
            _num(row.get("nav")), _num(row.get("pe")),
            float(profit_mn) * 1e6 if profit_mn is not None else None,
            float(tci_mn) * 1e6 if tci_mn is not None else None,
            _num(row.get("dividend_yield")),
            _num(row.get("cash_div_pct")), _num(row.get("stock_div_pct")),
            row["fetched_at"], row["source"], job_id,
        )
        counts["fundamentals"] += 1

    for _, row in bundle.quarterly.iterrows():
        await pool.execute(
            """
            INSERT INTO fundamentals_quarterly
                (ticker, fiscal_year, quarter, eps_basic, eps_diluted,
                 period_end_price, fetched_at, source)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT (ticker, fiscal_year, quarter) DO UPDATE SET
                eps_basic = EXCLUDED.eps_basic, eps_diluted = EXCLUDED.eps_diluted,
                period_end_price = EXCLUDED.period_end_price, fetched_at = EXCLUDED.fetched_at
            """,
            row["ticker"], int(row["fiscal_year"]), int(row["quarter"]),
            _num(row.get("eps_basic")), _num(row.get("eps_diluted")),
            _num(row.get("period_end_price")), row["fetched_at"], row["source"],
        )
        counts["quarterly"] += 1

    for _, row in bundle.shareholding.iterrows():
        await pool.execute(
            """
            INSERT INTO shareholding_history
                (ticker, as_on_date, sponsor_pct, govt_pct, institution_pct,
                 foreign_pct, public_pct, fetched_at, source)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (ticker, as_on_date) DO UPDATE SET
                sponsor_pct = EXCLUDED.sponsor_pct, govt_pct = EXCLUDED.govt_pct,
                institution_pct = EXCLUDED.institution_pct, foreign_pct = EXCLUDED.foreign_pct,
                public_pct = EXCLUDED.public_pct, fetched_at = EXCLUDED.fetched_at
            """,
            row["ticker"], row["as_on_date"], _num(row.get("sponsor_pct")),
            _num(row.get("govt_pct")), _num(row.get("institution_pct")),
            _num(row.get("foreign_pct")), _num(row.get("public_pct")),
            row["fetched_at"], row["source"],
        )
        counts["shareholding"] += 1

    for _, row in bundle.actions.iterrows():
        await pool.execute(
            """
            INSERT INTO corporate_actions
                (ticker, fiscal_year, action_type, value_pct, ratio_text, ratio, source)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (ticker, fiscal_year, action_type) DO UPDATE SET
                value_pct = EXCLUDED.value_pct, ratio_text = EXCLUDED.ratio_text,
                ratio = EXCLUDED.ratio
            """,
            row["ticker"], int(row["fiscal_year"]), row["action_type"],
            _num(row.get("value_pct")), row.get("ratio_text"), _num(row.get("ratio")),
            row["source"],
        )
        counts["actions"] += 1

    m = bundle.company_meta
    if m.get("ticker"):
        # COALESCE: transiently blank page section must not wipe stored values.
        # Consequence: a genuinely removed rating can never clear itself (accepted, see design §3.5).
        await pool.execute(
            """
            UPDATE companies SET
                face_value         = COALESCE($2,  face_value),
                market_lot         = COALESCE($3,  market_lot),
                scrip_code         = COALESCE($4,  scrip_code),
                electronic_share   = COALESCE($5,  electronic_share),
                operational_status = COALESCE($6,  operational_status),
                short_loan_mn      = COALESCE($7,  short_loan_mn),
                long_loan_mn       = COALESCE($8,  long_loan_mn),
                loan_as_on         = COALESCE($9,  loan_as_on),
                credit_rating_st   = COALESCE($10, credit_rating_st),
                credit_rating_lt   = COALESCE($11, credit_rating_lt),
                delisting_remark   = COALESCE($12, delisting_remark),
                ir_url             = COALESCE($13, ir_url),
                psi_url            = COALESCE($14, psi_url)
            WHERE ticker = $1
            """,
            m["ticker"], _num(m.get("face_value")), m.get("market_lot"), m.get("scrip_code"),
            m.get("electronic_share"), m.get("operational_status"),
            _num(m.get("short_loan_mn")), _num(m.get("long_loan_mn")), m.get("loan_as_on"),
            m.get("credit_rating_st"), m.get("credit_rating_lt"), m.get("delisting_remark"),
            m.get("ir_url"), m.get("psi_url"),
        )
        counts["companies"] += 1

    return counts
```

`_load_one` becomes:

```python
async def _load_one(pool, ticker, adapter, job_id) -> dict:
    try:
        bundle = await adapter.fetch_company_bundle(ticker=ticker)
    except AdapterError as exc:
        logger.warning("fundamentals_hist_failed ticker=%s error=%s", ticker, exc)
        return {"ticker": ticker, "status": "failed", "counts": {}}
    counts = await _write_bundle(pool, bundle, job_id)
    return {"ticker": ticker, "status": "ok", "counts": counts}
```

And in `bulk_load_fundamentals_historical`, accumulate per-table totals into `summary` (keep `total_upserted` = sum of fundamentals counts for the scheduler's `records_inserted` mapping):

```python
    summary: dict = {"ok": 0, "failed": 0, "total_upserted": 0,
                     "quarterly": 0, "shareholding": 0, "actions": 0}
    ...
            if r["status"] == "ok":
                summary["ok"] += 1
                c = r["counts"]
                summary["total_upserted"] += c.get("fundamentals", 0)
                summary["quarterly"] += c.get("quarterly", 0)
                summary["shareholding"] += c.get("shareholding", 0)
                summary["actions"] += c.get("actions", 0)
```

- [ ] **Step 4: Run tests**

Run: `docker exec dse_worker python -m pytest tests/unit/test_fundamentals_loader.py tests/unit/test_dse_company_info.py -v --no-cov`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add extraction/bulk_load/fundamentals_historical_loader.py tests/unit/test_fundamentals_loader.py
git commit -m "feat(loader): write quarterly/shareholding/actions/company-meta from bundle"
```

---

### Task 9: End-to-end verify against live DB

**Files:** none (verification only)

- [ ] **Step 1: Run the loader for the four fixture tickers inside docker**

Run: `docker exec dse_worker python -m extraction.bulk_load.fundamentals_historical_loader CITYBANK GP SQURPHARMA FAMILYTEX`
Expected: log `fundamentals_hist_done ok=4 failed=0` (FAMILYTEX may legitimately fail with "no historical year rows" if the Z-cat page is bare — `ok=3 failed=1` acceptable; note which).

- [ ] **Step 2: Spot-check rows**

Run: `make db-shell` then:

```sql
SELECT fiscal_year, eps, net_profit_bdt, cash_div_pct, stock_div_pct
FROM fundamentals WHERE ticker='CITYBANK' ORDER BY fiscal_year DESC LIMIT 25;
SELECT * FROM shareholding_history WHERE ticker='CITYBANK' ORDER BY as_on_date;
SELECT * FROM corporate_actions WHERE ticker='CITYBANK' AND action_type='right_issue';
SELECT * FROM fundamentals_quarterly WHERE ticker='CITYBANK';
SELECT face_value, scrip_code, long_loan_mn, ir_url FROM companies WHERE ticker='CITYBANK';
```

Expected: 2025 net_profit_bdt = 13242270000.00; dividend years back to 2004; 3 shareholding rows; 3 right issues; Q1-2026 row; companies row populated.

- [ ] **Step 3: Re-run loader, verify counts identical (idempotency)** — same command as Step 1, then `SELECT count(*) FROM fundamentals WHERE ticker='CITYBANK';` must not grow.

---

### Task 10: Scheduler metrics + adapter health check hardening

**Files:**
- Modify: `extraction/scheduler.py:587-603` (`job_weekly_fundamentals`)
- Modify: `extraction/adapters/dse_direct/company_info.py` (`health_check`)
- Test: `tests/unit/test_dse_company_info.py`

- [ ] **Step 1: Update `job_weekly_fundamentals`** — after `summary = await bulk_load_fundamentals_historical()`:

```python
        ctx["records_inserted"] = summary["total_upserted"]
        ctx["records_quarterly"] = summary["quarterly"]
        ctx["records_shareholding"] = summary["shareholding"]
        ctx["records_actions"] = summary["actions"]
```

(matches the price_gap_backfill metrics-mapping pattern; `pipeline_jobs` persists `records_*` keys.)

- [ ] **Step 2: Harden `health_check`** — replace body so a column-mapping break is detected, not just reachability:

```python
    async def health_check(self) -> bool:
        try:
            bundle = await self.fetch_company_bundle(ticker="GP")
            yearly = bundle.yearly
            return (
                not yearly.empty
                and yearly["eps"].notna().any()
                and "net_profit_mn" in yearly.columns
            )
        except Exception:
            return False
```

- [ ] **Step 3: Run scheduler-related tests**

Run: `docker exec dse_worker python -m pytest tests/unit/test_health_checks.py tests/unit/test_recovery.py -v --no-cov`
Expected: PASS (no registry changes — job already registered).

- [ ] **Step 4: Commit**

```bash
git add extraction/scheduler.py extraction/adapters/dse_direct/company_info.py
git commit -m "feat(scheduler): per-table fundamentals metrics; bundle-aware health check"
```

---

### Task 11: Quality rules (P3, FR8)

**Files:**
- Modify: `extraction/quality.py`
- Test: `tests/unit/test_quality.py` (extend if exists, else create)

- [ ] **Step 1: Write the failing tests**

```python
import pandas as pd


def test_shareholding_sum_rule():
    from extraction.quality import _check_shareholding_sum

    ok = pd.DataFrame([{"ticker": "GP", "sponsor_pct": 90, "govt_pct": 0,
                        "institution_pct": 6.63, "foreign_pct": 0.8, "public_pct": 2.57}])
    bad = pd.DataFrame([{"ticker": "X", "sponsor_pct": 50, "govt_pct": 0,
                         "institution_pct": 10, "foreign_pct": 5, "public_pct": 10}])
    assert _check_shareholding_sum(ok) == []
    fails = _check_shareholding_sum(bad)
    assert len(fails) == 1 and fails[0].rule == "shareholding_sum"


def test_eps_bounds_rule():
    from extraction.quality import _check_eps_bounds

    df = pd.DataFrame([{"ticker": "X", "eps": 2000.0, "nav": 5.0},
                       {"ticker": "Y", "eps": 8.71, "nav": 40.67}])
    fails = _check_eps_bounds(df)
    assert len(fails) == 1 and fails[0].ticker == "X"
```

- [ ] **Step 2: Run to verify failure**

Run: `docker exec dse_worker python -m pytest tests/unit/test_quality.py -v --no-cov`
Expected: FAIL — ImportError

- [ ] **Step 3: Implement** (add to `quality.py`; wire into `run_quality_checks` behind column-presence guards so existing streams are untouched)

```python
def _check_shareholding_sum(df: pd.DataFrame) -> list[QualityFailure]:
    cols = ["sponsor_pct", "govt_pct", "institution_pct", "foreign_pct", "public_pct"]
    if not all(c in df.columns for c in cols):
        return []
    failures = []
    for _, row in df.iterrows():
        vals = [row.get(c) for c in cols]
        if any(v is None or pd.isna(v) for v in vals):
            continue
        total = sum(float(v) for v in vals)
        if not (99.0 <= total <= 101.0):
            failures.append(QualityFailure(
                rule="shareholding_sum", ticker=row.get("ticker"),
                detail=f"components sum to {total:.2f}", severity="warning"))
    return failures


def _check_eps_bounds(df: pd.DataFrame) -> list[QualityFailure]:
    failures = []
    if "eps" in df.columns:
        for _, row in df.iterrows():
            eps = row.get("eps")
            if eps is not None and not pd.isna(eps) and abs(float(eps)) > 1000:
                failures.append(QualityFailure(
                    rule="eps_bounds", ticker=row.get("ticker"),
                    detail=f"|eps|={float(eps):.2f} > 1000", severity="warning"))
    if "nav" in df.columns:
        for _, row in df.iterrows():
            nav = row.get("nav")
            if nav is not None and not pd.isna(nav) and float(nav) < -10000:
                failures.append(QualityFailure(
                    rule="nav_bounds", ticker=row.get("ticker"),
                    detail=f"nav={float(nav):.2f} < -10000", severity="warning"))
    return failures
```

In `run_quality_checks`, after the existing checks:

```python
    failures += _check_shareholding_sum(df)
    failures += _check_eps_bounds(df)
```

(The design's `eps × pe ≈ year-end price` check needs a DB price lookup — out of `quality.py`'s pure-DataFrame scope. Implemented instead as a loader-side `quality_flag='suspect'` update; deferred to the P3 follow-up if not trivially added during Task 8 review.)

- [ ] **Step 4: Run tests**

Run: `docker exec dse_worker python -m pytest tests/unit/test_quality.py -v --no-cov`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add extraction/quality.py tests/unit/test_quality.py
git commit -m "feat(quality): shareholding-sum and eps/nav bounds rules"
```

---

### Task 12: Feature layer (P3, FR7)

**Files:**
- Modify: `ml/features/feature_store.py` (`build_fundamental_feature_vector` SELECT)
- Modify: `ml/features/fundamental_features.py`
- Test: `tests/unit/ml/test_fundamental_features.py` (extend)

- [ ] **Step 1: Write the failing tests**

```python
def test_track_record_features():
    import pandas as pd
    from ml.features.fundamental_features import compute_track_record_features

    yearly = pd.DataFrame({
        "fiscal_year":   [2021, 2022, 2023, 2024, 2025],
        "net_profit_bdt": [5494.16e6, 4781.26e6, 6384.66e6, 10143.46e6, 13242.27e6],
        "cash_div_pct":  [12.5, 10.0, 15.0, 12.5, 15.0],
        "stock_div_pct": [12.5, 2.0, 10.0, 12.5, 15.0],
    })
    f = compute_track_record_features(yearly, rights_count_10y=1,
                                      inst_flow_pp=-4.33, foreign_flow_pp=-0.61)
    # CAGR 3y: (13242.27 / 6384.66) ** (1/2)? No — window is last 4 closed years:
    # (13242.27/4781.26)^(1/3) - 1 ≈ 0.4043
    assert f["profit_cagr_3y"] == pytest.approx(0.4043, abs=1e-3)
    assert f["dividend_streak"] == 5
    assert f["cash_div_ratio_5y"] == pytest.approx(65.0 / (65.0 + 52.0), abs=1e-4)
    assert f["rights_count_10y"] == 1
    assert f["inst_flow_pp"] == pytest.approx(-4.33)


def test_track_record_cagr_null_on_sign_change():
    import math
    import pandas as pd
    from ml.features.fundamental_features import compute_track_record_features

    yearly = pd.DataFrame({
        "fiscal_year":   [2022, 2023, 2024, 2025],
        "net_profit_bdt": [-100e6, 50e6, 80e6, 120e6],
        "cash_div_pct":  [0, 0, 5.0, 5.0],
        "stock_div_pct": [0, 0, 0, 0],
    })
    f = compute_track_record_features(yearly, rights_count_10y=0,
                                      inst_flow_pp=None, foreign_flow_pp=None)
    assert math.isnan(f["profit_cagr_3y"])
    assert f["dividend_streak"] == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `docker exec dse_worker python -m pytest tests/unit/ml/test_fundamental_features.py -v --no-cov`
Expected: FAIL — ImportError `compute_track_record_features`

- [ ] **Step 3: Implement** (add to `fundamental_features.py`)

```python
def compute_track_record_features(
    yearly: pd.DataFrame,
    rights_count_10y: int,
    inst_flow_pp: float | None,
    foreign_flow_pp: float | None,
) -> dict[str, float]:
    """Track-record scalars from per-year fundamentals + pre-aggregated inputs.

    Rules (design §3.6): NULLs dropped per feature; windows use closed fiscal
    years only (caller passes closed years); CAGR is NaN on profit sign change.
    """
    df = yearly.sort_values("fiscal_year").reset_index(drop=True)
    profit = pd.to_numeric(df["net_profit_bdt"], errors="coerce")

    def _cagr(n_years: int) -> float:
        s = profit.dropna()
        if len(s) < n_years + 1:
            return np.nan
        first, last = float(s.iloc[-(n_years + 1)]), float(s.iloc[-1])
        if first <= 0 or last <= 0:  # sign change / nonpositive base → meaningless
            return np.nan
        return (last / first) ** (1.0 / n_years) - 1

    cash = pd.to_numeric(df["cash_div_pct"], errors="coerce").fillna(0)
    stock = pd.to_numeric(df["stock_div_pct"], errors="coerce").fillna(0)

    streak = 0
    for v in reversed(cash.tolist()):
        if v > 0:
            streak += 1
        else:
            break

    cash5, stock5 = float(cash.tail(5).sum()), float(stock.tail(5).sum())
    denom = cash5 + stock5

    return {
        "profit_cagr_3y": _cagr(3),
        "profit_cagr_5y": _cagr(5),
        "dividend_streak": float(streak),
        "cash_div_ratio_5y": cash5 / denom if denom > 0 else np.nan,
        "rights_count_10y": float(rights_count_10y),
        "inst_flow_pp": float(inst_flow_pp) if inst_flow_pp is not None else np.nan,
        "foreign_flow_pp": float(foreign_flow_pp) if foreign_flow_pp is not None else np.nan,
    }
```

In `feature_store.py` `build_fundamental_feature_vector`:
1. Add `f.net_profit_bdt, f.eps_basis,` to the SELECT column list and to the `pd.DataFrame(columns=[...])` list (drop `eps_basis` before the `to_numeric` loop — it's text: `df = df.drop(columns=["eps_basis"])` after extracting if needed, or exclude it from the numeric coercion loop).
2. After `features = compute_fundamental_features(df)`, fetch the two aggregates and merge:

```python
    rights = await pool.fetchval(
        """SELECT count(*) FROM corporate_actions
           WHERE ticker = $1 AND action_type = 'right_issue'
             AND fiscal_year >= EXTRACT(YEAR FROM now())::int - 10""", ticker)
    flows = await pool.fetchrow(
        """SELECT (last.institution_pct - first.institution_pct) AS inst_flow,
                  (last.foreign_pct - first.foreign_pct) AS foreign_flow
           FROM (SELECT * FROM shareholding_history WHERE ticker = $1
                 ORDER BY as_on_date ASC LIMIT 1) AS first,
                (SELECT * FROM shareholding_history WHERE ticker = $1
                 ORDER BY as_on_date DESC LIMIT 1) AS last""", ticker)

    from ml.features.fundamental_features import compute_track_record_features
    track = compute_track_record_features(
        df,
        rights_count_10y=rights or 0,
        inst_flow_pp=float(flows["inst_flow"]) if flows and flows["inst_flow"] is not None else None,
        foreign_flow_pp=float(flows["foreign_flow"]) if flows and flows["foreign_flow"] is not None else None,
    )
    for k, v in track.items():
        latest[k] = v
```

(`latest` is the existing Series built from `features.iloc[-1]`; check how the function returns and attach before the return statement — additive keys only, existing FEATURE_COLS consumers unaffected.)

- [ ] **Step 4: Run ML unit tests**

Run: `docker exec dse_worker python -m pytest tests/unit/ml/ -v --no-cov`
Expected: all PASS (existing scorer tests must not break — new keys are additive).

- [ ] **Step 5: Commit**

```bash
git add ml/features/fundamental_features.py ml/features/feature_store.py tests/unit/ml/test_fundamental_features.py
git commit -m "feat(ml): track-record features — profit CAGR, dividend streak, flows, rights count"
```

---

### Task 13: Full verification + docs

**Files:**
- Modify: `TODOS.md` (mark enrichment items), `CLAUDE.md` only if commands changed (they didn't)

- [ ] **Step 1: Full check**

Run: `docker exec dse_worker sh -c "cd /app && ruff check . && mypy --strict extraction/adapters/dse_direct/company_info.py extraction/bulk_load/fundamentals_historical_loader.py"`
Expected: clean. Fix any typing complaints (new functions are fully annotated; `Any` for bs4 Tags matches file convention).

- [ ] **Step 2: Full unit suite**

Run: `docker exec dse_worker python -m pytest tests/unit/ -v --no-cov`
Expected: all PASS.

- [ ] **Step 3: Update TODOS.md** — add/check items for fundamentals enrichment P1–P3 referencing the spec docs.

- [ ] **Step 4: Final commit**

```bash
git add TODOS.md
git commit -m "docs: fundamentals enrichment P1-P3 complete (spec 2026-06-12)"
```

---

## Self-review notes

- **Spec coverage:** FR1→Tasks 4–5, FR2→6+8, FR3→6–8, FR4→7–8, FR5→6+8, FR6→7, FR7→12, FR8→11 (eps×pe price check consciously deferred — noted in Task 11), D1–D3→4–5. Health signature→10. Migration→2.
- **Ground-truth values** in tests come from the live CITYBANK fixture captured in Task 1 (same page version as the 2026-06-12 design analysis). If DSE publishes new data between fixture capture and test writing, update the asserted values from the fixture, not from this plan.
- **Type consistency:** `_build_bundle` returns `CompanyBundle`; loader consumes `bundle.yearly/quarterly/shareholding/actions/company_meta`; `_write_bundle(pool, bundle, job_id) -> dict[str,int]` used in both tests and `_load_one`.
