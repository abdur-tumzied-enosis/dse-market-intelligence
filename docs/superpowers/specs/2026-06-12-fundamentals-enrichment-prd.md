# PRD: Historical Fundamentals Enrichment

**Date:** 2026-06-12
**Status:** Draft
**Source analysis:** Live inspection of `dsebd.org/displayCompany.php?name=CITYBANK` (2026-06-12) compared against current `fundamentals` schema and `DSEDirectCompanyInfoAdapter.fetch_historical()`.

## Problem

Our fundamentals layer stores six per-year fields (EPS, diluted EPS, NAV, P/E, cash dividend, stock dividend). The DSE company page — which we already scrape weekly — carries substantially more analyst-grade signal that we silently drop. A broker evaluating a company's track record needs answers our system cannot currently give:

1. **Is real profit growing?** "Profit for the year (mn)" is printed on the page; we never read it. `net_profit_bdt` is NULL for every row.
2. **Is smart money entering or exiting?** The page shows three dated shareholding snapshots (e.g. CITYBANK institutions 19.33% → 15.56% → 15.00% over five months). We keep only the latest and lose the trend.
3. **Has the company diluted shareholders?** Right-issue history ("1R:1 2010, 1R:1 2004, 1R:2 2003") is not captured at all.
4. **How long is the dividend track record?** The page lists cash dividends back to 2015 and bonus issues back to 2004, but our merge logic only keeps years present in the EPS table (typically the last 5) — **16 years of dividend history dropped** for CITYBANK.
5. **How leveraged is the company?** Short/long-term loan figures are on the page; the adapter hardcodes them to `None`.
6. **Quarterly earnings momentum?** The page shows only the current year's quarters. If we don't persist them as they appear, past quarters are unrecoverable.

Additionally, three parser defects degrade what we do capture (see Design doc §2).

## Users & use cases

- **Fundamental scorer (`ml/`):** profit CAGR, ROE trend, dividend consistency, dilution flags as features.
- **Stock page (frontend):** track-record panel — profit trend, dividend history, shareholding flow, corporate actions timeline.
- **Chat agent / analyze endpoints:** answer "is CITYBANK a good company?" with evidence (profit up 2.4x in 5y, but institutions exited 4.3pp in 5 months).
- **Screeners (future):** filter by dividend streak, sponsor holding, rights-issue count.

## Goals

| # | Goal | Metric |
|---|------|--------|
| G1 | Net profit populated for all tickers with DSE-published history | ≥90% of active equity tickers have ≥3 years of `net_profit_bdt` |
| G2 | Full dividend history captured | dividend years stored = years printed on DSE page (spot-check 20 tickers) |
| G3 | Shareholding trend accrues | `shareholding_history` gains ≥1 dated snapshot per ticker per weekly run; all 3 page periods captured on first run |
| G4 | Corporate actions (rights/bonus/cash) queryable as events | `corporate_actions` covers all years printed on page |
| G5 | Quarterly EPS persisted before it disappears | each weekly run upserts current-year quarters per ticker |
| G6 | Leverage + risk fields stored | loans, credit rating, operational status, face value, market lot on `companies` |
| G7 | No regression in existing pipeline | `make test` green; weekly job runtime stays < 30 min |

## Non-goals (this iteration)

- **Annual-report PDF extraction** (bank NPL/CAR/NIM, revenue for non-banks). The page's IR link is stored to enable this later, but PDF parsing is a separate effort (T7).
- **Revenue (`revenue_bdt`)** — not printed on the DSE page; comes with the PDF effort.
- New frontend panels — backend/data layer only; UI consumes later.
- Backfilling shareholding history older than what the page currently shows (only 3 periods exist).

## Functional requirements

### FR1 — Parser captures full per-year row (bug fixes)
- Extract "Profit for the year (mn)" and "Total Comprehensive Income" from the EPS/NAV table → `fundamentals.net_profit_bdt` (converted mn → BDT), `total_comprehensive_income_bdt`.
- Merge logic includes **dividend-only years** (years present in the Cash Dividend / Bonus Issue strings but absent from the EPS table). Such rows carry dividends with NULL EPS/NAV/PE.
- `eps_diluted` no longer falls back to the basic-EPS column; NULL when diluted not published.
- Record which EPS variant was stored (`eps_basis`: basic/diluted × original/restated).

### FR2 — Shareholding history
- Parse **all** dated "Share Holding Percentage [as on …]" rows (up to 3 per page) into `shareholding_history(ticker, as_on_date, sponsor, govt, institute, foreign, public)`.
- Idempotent on `(ticker, as_on_date)` — re-scrapes update, never duplicate.

### FR3 — Corporate actions
- Parse Right Issue, Bonus Issue, Cash Dividend th/td strings into `corporate_actions(ticker, year, action_type, value)` events.
- Right-issue ratio kept as text (`1R:2`) plus parsed numeric ratio.

### FR4 — Company risk/metadata fields
- Capture: face value, market lot, scrip code, electronic share flag, debut trading date, operational status, short/long-term loans (+ as-on date), credit ratings (short/long term), OTC/delisting remark, IR URL, PSI URL.
- Stored on `companies` (latest snapshot semantics).

### FR5 — Quarterly EPS persistence
- Parse the interim table (Q1/Q2/Half-yearly/Q3/9-months/Annual + "ending on" period labels + market price at period end) into `fundamentals_quarterly(ticker, fiscal_year, quarter, eps_basic, eps_diluted, period_end_price)`.
- Upsert on `(ticker, fiscal_year, quarter)`; weekly job accrues history going forward.

### FR6 — One fetch, all writes
- The weekly job must make **one HTTP request per ticker** and feed all the above writes from that single page fetch. No additional load on dsebd.org beyond today's weekly scrape (406 requests, 3 concurrent, 1.5 s delay).

### FR7 — Derived analyst metrics (feature layer)
- ROE = EPS / NAV per year (both values from the same `eps_basis` variant — no mixing restated EPS with original NAV); 3y/5y profit CAGR (window = last N+1 *closed* fiscal years, all present, NULL on profit sign change); dividend streak length (consecutive years ending at the latest **closed** fiscal year — a partial current year must not break or extend the streak); cash-vs-bonus mix; rights-issue count (10y); restatement gap flag (|original − restated| / |original| > 10%); institutional + foreign holding delta (latest vs oldest snapshot).
- Dividend-only year rows (FR1) carry NULL EPS/NAV/PE by design — feature functions must drop NULLs **per feature**, not per row, or dividend history would shrink the EPS sample.
- No cross-year dilution re-adjustment: DSE's "restated" EPS/NAV columns are already adjusted for subsequent bonus/rights issues; applying our own adjustment on top would double-count.
- Exposed via `ml/features/fundamental_features.py` for the scorer; no model retrain required in this iteration.

### FR8 — Quality rules
- Shareholding row sums to 100 ± 1.0 (page rounds components).
- `eps × pe` within tolerance of year-end price when both present — applies to **annual `fundamentals` rows only** (never quarterly rows); warn-level flag, not row rejection, and both factors read from the same stored variant (`eps_basis`).
- Negative NAV or |EPS| > 1000 flagged `suspect`.

## Rollout

| Phase | Scope | Depends on |
|---|---|---|
| P1 | Migration + parser fixes + new parse functions + fixtures (FR1–FR4 parse side) | — |
| P2 | Loader writes all tables; weekly job wiring; quarterly persistence (FR5, FR6) | P1 |
| P3 | Derived metrics + quality rules (FR7, FR8) | P2 |
| P4 (separate PRD) | Annual-report PDF extraction (bank metrics, revenue) | P2 |

## Risks

- **DSE table layout varies by company** (banks show extra "EPS — Continuing Operations" columns; CITYBANK row has 13 cells where GP has fewer). Mitigation: header-aware column mapping + fixtures from ≥4 structurally different companies (bank, telecom/MNC, Z-category, mutual fund excluded).
- **DSE page is the single source.** A silent HTML change breaks everything at once. Mitigation: structure-hash health check already exists (`extraction/health.py`); add the EPS-table header signature to it.
- **Dividend string ambiguity** ("12.50, 12.50%B" in the P/E table vs separate th/td strings). Use the th/td strings as source of truth; P/E-table dividend column ignored.
- **Loans/ratings often blank** for non-bank tickers — all new fields nullable; absence is signal, not error.
- **Profit CAGR coverage gap:** NULL-on-sign-change drops the feature for tickers that swung loss↔profit (estimated 5–10% of DSE, concentrated in Z-category). Scorer must treat missing CAGR as missing, not zero.
