# Design: Historical Fundamentals Enrichment

**Date:** 2026-06-12
**PRD:** [2026-06-12-fundamentals-enrichment-prd.md](2026-06-12-fundamentals-enrichment-prd.md)
**Status:** Draft

## 0. Current state

- `DSEDirectCompanyInfoAdapter` (`extraction/adapters/dse_direct/company_info.py`) has two fetch paths:
  - `fetch()` — single-row latest snapshot (feeds the `company_info` stream).
  - `fetch_historical()` — per-fiscal-year rows; called by `extraction/bulk_load/fundamentals_historical_loader.py`.
- `job_weekly_fundamentals` (scheduler, Sun 23:00 BD; catch-up rank 5 in `CATCHUP_REGISTRY`) runs the bulk loader weekly: 406 tickers, 3 concurrent, 1.5 s delay, ~18 min.
- Upsert idempotency via partial unique index `idx_fundamentals_ticker_fiscal_year` (migration 017).

## 1. Page anatomy (confirmed live, CITYBANK 2026-06-12)

| Page table | Content | Disposition |
|---|---|---|
| Trading/market info | LTP, ranges, volume | already covered by price streams — ignore |
| Basic info | authorized/paid-up cap, face value 10.0, market lot 1, instrument type, total securities, sector, debut date | partially captured → FR4 |
| Dividend/corporate th/td | Cash Div (→2015), Bonus (→2004), **Right Issue** ("1R:1 2010, 1R:1 2004, 1R:2 2003"), Year End, Reserve & Surplus, OCI | partially → FR1/FR3 |
| Interim table (11 rows) | Q1–Q3/9M/Annual EPS basic+diluted, "ending on" labels, market price at period end | not persisted → FR5 |
| Current P/E (2 tables) | 6-day series: current P/E basic/diluted, trailing P/E | out of scope (derivable from prices + EPS) |
| EPS/NAV table | Year \| EPS basic (orig/restated) \| diluted (orig/restated) \| EPS-CO \| NAV (orig/restated) \| PCO \| **Profit for the year (mn)** \| TCI | profit/TCI dropped → FR1 |
| P/E + Dividend table | year-end P/E, Dividend in %, Dividend Yield in % | pe captured; yield dropped → FR1 |
| Links | "Details of Financial Statement" (IR), "Price Sensitive Information" | parsed then discarded → FR4 |
| Listing/shareholding | listing year, category, electronic share, **3 dated shareholding rows** | only latest captured → FR2 |
| Status table | operational status, **short/long-term loans + as-on date**, latest dividend status, **credit rating (ST/LT)**, OTC/delisting | hardcoded None → FR4 |

Footnote on page: audited figures update after AGM; outstanding securities updates on record date. → weekly cadence is sufficient; no intraday freshness requirement.

## 2. Known parser defects (fix in P1)

| # | Defect | Location | Fix |
|---|---|---|---|
| D1 | Profit/TCI columns never read | `_parse_eps_nav_all_years` | header-grid mapping (§3.1) |
| D2 | `_merge_yearly_rows` keys on `eps_map ∪ pe_map` only — dividend years outside EPS table dropped (CITYBANK loses 2004–2020) | `company_info.py:306` | `years = eps ∪ pe ∪ div_by_year` |
| D3 | `eps_diluted = _fnum([3, 4])` falls through to basic column when diluted is dash → stores basic as diluted | `company_info.py:229` | no fallthrough; NULL when absent |

## 3. Component design

### 3.1 Header-grid table parsing (replaces index guessing)

The EPS/NAV table uses 3-row colspan/rowspan headers, and column count varies by company (banks add "EPS — Continuing Operations" sub-columns; CITYBANK data rows have 13 cells, GP fewer). Current hardcoded indices `[4,3,2,1]` are wrong for at least one layout.

New helper in `company_info.py`:

```python
def _expand_header_grid(table: Tag) -> list[str]:
    """Flatten the table's <th>/<td> header rows into one label per leaf column,
    expanding colspan/rowspan, joining parent labels:
    e.g. 'earnings per share(eps) > basic > original'."""
```

Column resolution then matches by label substring, not position:

| Target field | Label match (lowered) |
|---|---|
| eps (preferred) | `eps > basic > restated`, fallback `basic > original` |
| eps_diluted | `eps > diluted > restated`, fallback `diluted > original`; **no basic fallback** |
| nav | `nav per share > restated`, fallback `original` |
| net_profit_mn | `profit for the year` |
| tci_mn | `tci` |
| pe | `p/e ... basic` restated→original (P/E table) |
| dividend_yield | `dividend yield` |

`eps_basis` records which variant won (e.g. `basic_restated`). If header expansion fails (structure change), raise `AdapterError(retryable=False)` — loud failure preferred over silently mis-mapped columns; structure-hash health check (§3.7) alerts.

> **Implementation reality (2026-06-12):** on current DSE layouts the plain
> "Earnings per share (EPS)" columns are printed as dashes for ALL years on every
> fixture (CITYBANK, GP, SQURPHARMA) — published values live under the
> "EPS — Continuing Operations" columns. The implemented preference is therefore:
> plain basic/diluted (restated→original) first, then CO columns as fallback,
> recorded in `eps_basis` as `co_basic_original` etc. NAV prefers *original* over
> restated (restated is dash for the latest year on every fixture). The "Year"
> header spans two grid columns, so label→data-cell mapping subtracts a column
> offset (`_year_col_offset`).

### 3.2 New parse functions

All pure functions over `BeautifulSoup`, colocated in `company_info.py` (same pattern as existing `_parse_*`):

- `_parse_shareholding_all(soup) -> list[dict]` — every "Share Holding Percentage [as on …]" row; parses the date string (`Dec 31, 2025 (year ended)` / `May 31, 2026`) to `date`. Generalizes existing `_parse_shareholding` (which keeps only `share_rows[-1]`).
- `_parse_right_issues(raw: str) -> list[dict]` — `"1R:1 2010, 1R:2 2003"` → `[{year: 2010, ratio_text: "1R:1", ratio: 1.0}, {year: 2003, ratio_text: "1R:2", ratio: 0.5}]` (ratio = new shares per existing share).
- `_parse_status_table(soup) -> dict` — operational status, short/long-term loan (mn) + as-on date, credit rating ST/LT, OTC/delisting remark.
- `_parse_links(soup) -> dict` — IR URL, PSI URL.
- `_parse_quarterly_eps_full(soup) -> list[dict]` — extends `_parse_quarterly_eps`: fiscal year from the `Ending on … 202603`-style header labels (`YYYYMM` → fiscal year of that period end; all quarters on one page belong to that single fiscal year), basic + diluted per quarter, `period_end_price` row. Column mapping: Q1/Q2/Q3 are read directly; the "Half Yearly" and "9 Months" columns are cumulative and **ignored**; **Q4 is derived** as `Annual − 9 Months` when both are present, else NULL (the current parser's `eps_q4 = cells[6]` reads the *Annual* column, which is not Q4 — do not repeat that).
- Basic-info additions to `_parse_td_td`/`_parse_th_td` maps: face value, market lot, scrip code (from "Scrip Code" header table), electronic share, debut trading date.

### 3.3 New fetch path: `fetch_company_bundle()`

One HTTP GET → one parsed soup → structured bundle (FR6):

```python
@dataclass
class CompanyBundle:
    yearly: pd.DataFrame          # fundamentals rows (existing cols + net_profit, tci, dividend_yield, eps_basis)
    quarterly: pd.DataFrame       # fundamentals_quarterly rows
    shareholding: pd.DataFrame    # shareholding_history rows
    actions: pd.DataFrame         # corporate_actions rows
    company_meta: dict            # FR4 fields for companies UPDATE
```

`fetch_historical()` becomes a thin wrapper returning `bundle.yearly` (back-compat for existing callers/tests). The loader switches to `fetch_company_bundle()`.

### 3.4 Schema — migration `036_fundamentals_enrichment.sql`

```sql
-- 1. fundamentals: per-year additions
ALTER TABLE fundamentals
    ADD COLUMN IF NOT EXISTS total_comprehensive_income_bdt NUMERIC(20, 2),
    ADD COLUMN IF NOT EXISTS dividend_yield_pct NUMERIC(8, 4),
    ADD COLUMN IF NOT EXISTS eps_basis TEXT;
-- net_profit_bdt already exists (004); loader writes profit_mn * 1e6.

-- 2. shareholding history (FR2)
-- NUMERIC(7,4): 100.0000 must fit (fully govt/sponsor-held tickers exist);
-- the NUMERIC(6,4) used in 004 caps at 99.9999 — do not copy that.
CREATE TABLE IF NOT EXISTS shareholding_history (
    id              BIGSERIAL    PRIMARY KEY,
    ticker          TEXT         NOT NULL REFERENCES companies (ticker),
    as_on_date      DATE         NOT NULL,
    sponsor_pct     NUMERIC(7,4),
    govt_pct        NUMERIC(7,4),
    institution_pct NUMERIC(7,4),
    foreign_pct     NUMERIC(7,4),
    public_pct      NUMERIC(7,4),
    fetched_at      TIMESTAMPTZ  NOT NULL,
    source          TEXT         NOT NULL,
    ingested_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, as_on_date)
);
CREATE INDEX IF NOT EXISTS idx_shareholding_ticker_date
    ON shareholding_history (ticker, as_on_date DESC);

-- 3. corporate actions (FR3)
CREATE TABLE IF NOT EXISTS corporate_actions (
    id          BIGSERIAL   PRIMARY KEY,
    ticker      TEXT        NOT NULL REFERENCES companies (ticker),
    fiscal_year SMALLINT    NOT NULL,
    action_type TEXT        NOT NULL CHECK (action_type IN ('cash_div','stock_div','right_issue')),
    value_pct   NUMERIC(8,4),          -- cash/stock dividend, % of face value (DSE convention:
                                       -- 15% on 10 BDT face = 1.50 BDT/share), gross of tax
    ratio_text  TEXT,                  -- right issue raw, e.g. '1R:2'
    ratio       NUMERIC(8,4),          -- right issue: new shares per existing
    source      TEXT        NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, fiscal_year, action_type)
);
-- One action per type per year matches the SOURCE: the DSE th/td strings print one
-- combined figure per year ("15% 2025"). Interim/final dividend granularity lives in
-- company_announcements (migration 016), not here.

-- 4. quarterly EPS (FR5)
CREATE TABLE IF NOT EXISTS fundamentals_quarterly (
    id               BIGSERIAL   PRIMARY KEY,
    ticker           TEXT        NOT NULL REFERENCES companies (ticker),
    fiscal_year      SMALLINT    NOT NULL,
    quarter          SMALLINT    NOT NULL CHECK (quarter BETWEEN 1 AND 4),
    eps_basic        NUMERIC(10,4),
    eps_diluted      NUMERIC(10,4),
    period_end_price NUMERIC(12,4),
    fetched_at       TIMESTAMPTZ NOT NULL,
    source           TEXT        NOT NULL,
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, fiscal_year, quarter)
);

-- 5. companies: risk/meta fields (FR4, latest-snapshot semantics)
ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS face_value         NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS market_lot         INTEGER,
    ADD COLUMN IF NOT EXISTS scrip_code         TEXT,
    ADD COLUMN IF NOT EXISTS electronic_share   BOOLEAN,
    ADD COLUMN IF NOT EXISTS debut_trading_date DATE,
    ADD COLUMN IF NOT EXISTS operational_status TEXT,
    ADD COLUMN IF NOT EXISTS short_loan_mn      NUMERIC(20,2),
    ADD COLUMN IF NOT EXISTS long_loan_mn       NUMERIC(20,2),
    ADD COLUMN IF NOT EXISTS loan_as_on         DATE,
    ADD COLUMN IF NOT EXISTS credit_rating_st   TEXT,
    ADD COLUMN IF NOT EXISTS credit_rating_lt   TEXT,
    ADD COLUMN IF NOT EXISTS delisting_remark   TEXT,
    ADD COLUMN IF NOT EXISTS ir_url             TEXT,
    ADD COLUMN IF NOT EXISTS psi_url            TEXT;
```

Decisions:
- **`net_profit_bdt` reused** (absolute BDT, mn × 1e6) rather than a new `_mn` column — avoids two columns meaning the same thing; loader owns the conversion.
- **Dividend-only years** (D2 fix) insert rows with NULL EPS/NAV/PE — partial unique index from 017 already covers them (`fiscal_year IS NOT NULL`).
- **Loans on `companies`, not `fundamentals`** — page shows one dated snapshot, not history; `loan_as_on` preserves the date. If trend matters later, promote to its own table.

### 3.5 Loader changes (`fundamentals_historical_loader.py`)

`_load_one()` becomes: fetch bundle → 5 write groups in one connection-acquired sequence (not a transaction across tickers; per-ticker atomicity is enough). Every INSERT carries explicit `ON CONFLICT … DO UPDATE` (the migration only declares the constraints; conflict handling is loader SQL, same as the existing fundamentals statement):

1. `fundamentals` — existing `ON CONFLICT (ticker, fiscal_year) WHERE fiscal_year IS NOT NULL DO UPDATE` extended with the new columns (`net_profit_bdt`, `total_comprehensive_income_bdt`, `dividend_yield_pct`, `eps_basis`).
2. `fundamentals_quarterly` — `ON CONFLICT (ticker, fiscal_year, quarter) DO UPDATE`; skip all-NULL quarters.
3. `shareholding_history` — `ON CONFLICT (ticker, as_on_date) DO UPDATE` (a republished snapshot for the same date is a correction; latest wins, `fetched_at` records when).
4. `corporate_actions` — `ON CONFLICT (ticker, fiscal_year, action_type) DO UPDATE`.
5. `companies` UPDATE of FR4 fields with `COALESCE($n, col)` so a transiently blank page section doesn't wipe stored values. Scope notes: COALESCE guards NULL only — `0`/`false` are valid values and pass through; and a genuinely *removed* rating/status can therefore never clear itself — acceptable for snapshot fields, revisit if staleness ever matters.

No double-conversion risk on `net_profit_bdt`: every run re-parses the page value (mn) and writes `value * 1e6` — the upsert overwrites; nothing reads the stored value back. Loan figures stay in mn (column names say `_mn`); no conversion at all.

Summary dict gains per-table counts → `job_weekly_fundamentals` maps into `pipeline_jobs` metrics (same pattern as the recent price_gap_backfill metrics fix, commit fb0d0e6).

Runtime unchanged: same request count, parse cost +~10 ms/page, write cost trivially higher. Stays well under the 30-min budget (G7).

### 3.6 Derived metrics (P3) — `ml/features/fundamental_features.py`

New feature functions (pure, over DataFrames loaded by `feature_store`):

| Feature | Definition |
|---|---|
| `roe` | eps / nav per year (NULL-safe) |
| `profit_cagr_3y`, `profit_cagr_5y` | CAGR over net_profit_bdt; requires ≥4/≥6 yrs; NULL on sign change (negative-to-positive profit makes CAGR meaningless) |
| `dividend_streak` | consecutive years (ending latest) with cash_div_pct > 0 |
| `cash_div_ratio_5y` | Σcash / (Σcash + Σstock) over last 5 yrs |
| `rights_count_10y` | count of right_issue actions in last 10 yrs |
| `restatement_flag` | any year where \|orig − restated\| / \|orig\| > 0.10 (needs both variants → parser exposes both via eps_basis + stored value; flag computed at parse time and ORed per ticker) |
| `inst_flow_pp`, `foreign_flow_pp` | latest minus oldest shareholding snapshot, percentage points |

Rules that apply to every feature above:
- **Variant discipline:** ROE and any EPS/NAV-derived metric use values stored under the same `eps_basis`; never mix restated EPS with original NAV.
- **NULL handling:** dividend-only year rows have NULL EPS/NAV/PE by design (D2 fix) — drop NULLs per feature, never per row.
- **Closed years only:** streaks and CAGR windows end at the latest closed fiscal year (page footnote: audited figures land after AGM); a partial current year is excluded.
- **No dilution re-adjustment:** DSE restated columns are already adjusted for later bonus/rights issues.

Wire into `fundamental_scorer` feature set behind additive columns — existing model unaffected until next quarterly retrain (which picks up new features automatically per training pipeline). **Required change:** `ml/features/feature_store.py` `build_fundamental_feature_vector` SELECTs an explicit column list from `fundamentals` — new columns must be added there or the features silently compute on nothing.

### 3.7 Quality & health (P3)

- `extraction/quality.py`: new rules — shareholding sum ∈ [99.0, 101.0]; |eps| ≤ 1000; nav > −10000; `eps × pe` vs year-end close within ±25% **warn-only**, **annual rows only**, both factors from the same stored variant (`quality_flag='suspect'`, row still stored; restated/original mismatch makes this noisy).
- `extraction/health.py`: add EPS/NAV table header signature to the structure hash for `displayCompany.php` so layout drift alerts before the weekly job mass-fails.

### 3.8 Testing

Per repo convention (smoke → pickle fixtures → offline unit tests):

1. **Smoke** (`tests/smoke/test_dse_company_smoke.py`): fetch + pickle raw HTML for structurally distinct tickers: `CITYBANK` (bank, EPS-CO columns, 3 shareholding rows, rights history), `GP` (MNC, simple layout), `SQURPHARMA` (pharma), one Z-category ticker (sparse data). Live-window guard same as existing smoke tests.
2. **Unit** (`tests/unit/test_dse_company_info.py` extensions):
   - header-grid expansion per fixture → expected column map;
   - D1/D2/D3 regression tests (CITYBANK: profit 2025 = 13,242.27 mn; dividend years include 2004; diluted EPS NULL where page shows dash);
   - right-issue string parsing edge cases (`1R:2`, missing spaces, empty);
   - shareholding date parsing (`(year ended)` suffix, month-name formats);
   - bundle integration: one soup → all five outputs.
3. **Loader unit** (`tests/unit/test_fundamentals_loader.py`): upsert idempotency (run twice, counts stable), COALESCE company update, NaN→None coercion for new columns.
4. **Migration**: `tests/integration/test_schema_consistency.py` picks up new tables automatically; add the four new unique constraints to its expectations.

## 4. Out of scope / future

- Trailing-P/E daily series (derivable: close / annualized EPS).
- Annual-report PDF pipeline (P4) — `ir_url` now stored, unblocks it.
- PSI announcement scraping — `psi_url` stored; `company_announcements` (migration 016) is the natural sink.
- Sector-relative metrics (rank vs sector peers) — scorer-side, after P3 features land.

## 5. File touch list

| File | Change |
|---|---|
| `db/migrations/036_fundamentals_enrichment.sql` | new |
| `extraction/adapters/dse_direct/company_info.py` | header-grid parser, new `_parse_*`, `fetch_company_bundle`, D1–D3 fixes |
| `extraction/bulk_load/fundamentals_historical_loader.py` | bundle writes, per-table summary |
| `extraction/scheduler.py` | `job_weekly_fundamentals` metrics mapping (minor) |
| `extraction/quality.py` | new rules |
| `extraction/health.py` | EPS-table header signature |
| `ml/features/fundamental_features.py` | P3 features |
| `ml/features/feature_store.py` | add new fundamentals columns to explicit SELECT |
| `tests/smoke/test_dse_company_smoke.py` | new fixtures |
| `tests/unit/test_dse_company_info.py`, `tests/unit/test_fundamentals_loader.py` | new/extended |
