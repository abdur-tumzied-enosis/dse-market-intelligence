# Fundamental Scorer Redesign — Design

**Date:** 2026-06-14
**Status:** Approved (design), pending implementation plan
**Owner:** ml/train, ml/features, ml/models, ml/inference, ml/explain

## Problem

The fundamental scorer is underpowered and has a train/serve skew bug:

1. **Skew bug.** `feature_store.build_fundamental_feature_vector` already computes 7
   track-record features (profit_cagr_3y/5y, dividend_streak, cash_div_ratio_5y,
   rights_count_10y, inst_flow_pp, foreign_flow_pp) and appends them to the serve
   vector. But `FEATURE_COLS` lists only 9 basic features, so `predict_proba` does
   `X[FEATURE_COLS]` and **silently discards the 7 track features at serve time**.
   Training never computes them at all.
2. **Untapped data.** Rich enrichment data is loaded but unused by the model:
   debt (short/long loan), comprehensive income, shareholding time-series,
   quarterly EPS, dividend yield. Credit rating exists as a column but has **0 rows**.
3. **Not beginner-readable.** Output is a single probability. Target users are
   complete beginners who do not understand markets — they need an interpretable,
   decomposed score with plain-language reasons.

## Data Reality (verified against live DB, 2026-06-14)

| Metric | Value |
|---|---|
| fundamentals rows | 4837 |
| with eps + fiscal_year | 1903 |
| distinct tickers | 398 |
| fiscal_year range | 1983–2025 |
| net_profit_bdt non-null | 1821 |
| comprehensive_income non-null | 1809 |
| shareholding_history rows | 1147 |
| corporate_actions rows | 5032 (rights: 93) |
| fundamentals_quarterly rows | 917 |
| companies with loan data | 383 |
| companies with credit rating | **0** (drop feature) |
| stock_prices rows | 1,101,497 |

**Trainable label set** (fundamentals row that has both a price near FY-end and a
forward price):

| Label horizon | Trainable rows | Distinct FYs |
|---|---|---|
| 6-month forward | 1860 | 14 |
| 12-month forward | 1645 | 13 |

**12m per-year distribution:** FY2012–2019 are sparse (1–39 rows each). The usable
cross-sectional cohorts are **FY2020 (148), 2021 (356), 2022 (357), 2023 (357),
2024 (344)** = 1562 of 1645 rows. FY2025 cannot form a 12m label until ~2027.

**Consequence:** ~5 dense cross-sectional cohorts. Walk-forward CV yields ~4 folds.
Small-data regime → keep XGBoost shallow, add calibration, and report **rank IC**,
not AUC alone.

## Label

**Primary:** forward **12-month** return, **sector-and-year-neutralized**.

- For each row, raw_return = (price_12m_later − price_at_fy_end) / price_at_fy_end.
  - price_at_fy_end: last close in [FY-end Oct 1 .. FY+1 Jan 31].
  - price_12m_later: first close in [FY+1 Oct 1 .. FY+2 Mar 31].
- neutralized_return = raw_return − median(raw_return) over the same
  (fiscal_year, sector) cohort. Removes sector beta and market beta.
- **label = 1 if neutralized_return > 0** within the fiscal_year (top half).
  Top-half (not tertile) chosen because small cohorts make a balanced ~50/50 split
  more learnable.

**Secondary/fallback:** 6-month horizon, same neutralization. Has one more cohort;
used if 12m proves too sparse for a given run. The horizon is a config switch, not a
fork — both share the same label-construction code path.

Sector-neutralization guard: cohorts with < 5 names in a (year, sector) bucket fall
back to year-only neutralization (sector median is too noisy with < 5 names).

## Approach (chosen: B — ML score + rule-based pillar scorecard)

Considered:
- **A — ML + SHAP sentences:** faithful but SHAP phrasing unintuitive, uneven pillar coverage.
- **B — ML score + deterministic pillar scorecard (CHOSEN):** XGBoost gives the
  headline rank; 6 deterministic pillars give intuitive, robust decomposition.
- **C — XGBoost + linear surrogate:** two models can disagree → confuses beginners.

**B** is chosen: beginners instantly grasp "scores 82 — strong Growth, weak Safety".
Pillars are sensible standalone and double as engineered meta-features for the model.

## Features

Grouped into the 6 beginner pillars. Each raw feature null-imputed via stored medians.

| Pillar | Features |
|---|---|
| **Growth** | eps_growth_1yr, eps_growth_3yr, profit_cagr_3y, profit_cagr_5y, nav_growth, quarterly EPS YoY momentum |
| **Quality** | eps_consistency, roe, comprehensive_income / net_profit (earnings quality), profit-positive streak |
| **Value** | pe_vs_sector, pb_ratio, div_yield, dividend_yield_pct |
| **Dividends** | dividend_streak, cash_div_ratio_5y, payout_ratio |
| **Financial Safety** | total_loan / market_cap (leverage), total_loan / net_profit (years-to-repay), rights_count_10y (dilution; lower = safer) |
| **Ownership** | inst_flow_pp, foreign_flow_pp, institution_pct level, foreign_pct level |

Dropped: credit_rating_lt/st (0 rows).

Leverage uses only available columns: total_loan_mn = COALESCE(short_loan_mn,0) +
COALESCE(long_loan_mn,0); denominators market_cap_bdt and net_profit_bdt. Rows
missing loan data get NaN (imputed), not 0 — absence ≠ zero debt.

## Components

1. **`ml/features/fundamental_features.py`** — extend `compute_fundamental_features`
   with the new per-year features; add quarterly-momentum helper (consumes
   fundamentals_quarterly) and a leverage helper (consumes companies loan cols).
   Add `compute_pillar_scores(feature_row, cross_sectional_percentiles) -> dict[str,float]`
   returning the 6 pillar scores (0–100) as cross-sectional percentiles of pillar
   constituents within the scoring cohort.
2. **`ml/models/fundamental_scorer.py`** — `FEATURE_COLS` becomes the full union.
   Wrap XGBoost in isotonic `CalibratedClassifierCV` (prefit on a holdout fold) so
   `predict_proba` returns a trustworthy probability. Pickle stores: calibrated
   model, medians, FEATURE_COLS, SHAP background sample, feature metadata.
3. **`ml/train/train_fundamental.py`** — rewrite: new label (sector+year-neutral
   12m), walk-forward-by-fiscal-year CV with 1-year embargo, per-fold Spearman rank
   IC + AUC + top-decile precision, then fit + calibrate final model on full set.
4. **`ml/features/feature_store.py`** — `build_fundamental_feature_vector` calls the
   SAME feature-builder as training (single source of truth). Eliminates skew.
5. **`ml/explain/fundamental_explainer.py`** (new) — per ticker, produce:
   headline score (0–100), 6 pillar scores, top-3 plain-language drivers. Each
   driver = {feature, raw_value, plain_sentence, polarity good/bad}, generated from
   a per-feature template table + SHAP attribution for ranking which drivers to show.

## Data Flow

```
fundamentals + quarterly + corporate_actions + shareholding_history + companies(loan)
        │
        ▼
compute_fundamental_features  ──(shared)──>  TRAIN: label join → walk-forward CV → calibrate → pkl
        │                                    SERVE: feature_store → scorer.predict_proba → stock_scores
        ▼
compute_pillar_scores ─┐
                       ▼
        fundamental_explainer → {headline, pillars[6], drivers[3]} → beginner UI
```

## Validation & Success Criteria

- **Metric:** per-fold Spearman rank IC (primary), ROC-AUC, top-decile precision.
- **CV:** expanding walk-forward by fiscal_year with a 1-year embargo between
  train and test (forward-return label overlaps the next cohort's feature date).
- **Bar:** mean rank IC meaningfully > 0 and > the current 9-feature baseline on the
  same folds. Calibration: reliability curve roughly diagonal on the last fold.
- **Skew check:** a test asserting train feature columns == serve feature columns
  (regression guard for the bug that motivated this work).

## Error Handling

- Missing feature → NaN → median impute (never silent 0 for debt).
- Cohort with one class in a fold → skip that fold's AUC/IC (already handled; keep).
- Sparse (year, sector) bucket → fall back to year-only neutralization.
- Insufficient data overall (< ~200 trainable rows) → warn and abort, do not ship a
  model that cannot generalize.

## Out of Scope (YAGNI)

- No new DB migration (all data exists).
- No credit-rating feature (0 rows).
- No deep model / LSTM (that is the separate price model).
- No UI work in this spec — explainer emits a structured payload; UI consumes later.
- FY2025+ retraining cadence is operational, not part of this redesign.
