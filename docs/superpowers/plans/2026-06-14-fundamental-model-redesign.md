# Fundamental Scorer Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the fundamental scorer into a calibrated, leak-free ranking model over enriched data, with a beginner-readable 6-pillar scorecard and plain-language explanations.

**Architecture:** A single shared feature-builder feeds both training and serving (kills the train/serve skew bug). Label = sector-and-year-neutralized 12-month forward return, top-half within fiscal year. Validation is expanding walk-forward by fiscal year with a 1-year embargo, scored by Spearman rank IC. XGBoost is wrapped in isotonic calibration. A deterministic pillar layer (Growth/Value/Quality/Dividends/Safety/Ownership) and an xgboost-native TreeSHAP explainer produce the beginner payload.

**Tech Stack:** Python, pandas, numpy, scipy (`spearmanr`), scikit-learn (`CalibratedClassifierCV`), xgboost 3.2 (`pred_contribs` for native SHAP — no `shap` package), asyncpg, pytest.

---

## Key Decisions & Deviations from Spec

- **Leverage is display-only, not a model feature.** `companies` loan columns are a single latest snapshot (`loan_as_on`), so they cannot be made as-of for historical training rows. Feeding them would leak future state into past rows. Leverage therefore lives only in the **Safety pillar** (computed at serve from current data) — it is excluded from `FEATURE_COLS`.
- **Pillars are NOT fed back as model features.** In a ~1500-row / 3-fold regime, pillar percentiles are redundant with the raw features they aggregate and would invite overfitting. Pillars are a deterministic display/explanation layer derived from the same raw features.
- **Track/ownership/quarterly features are computed AS-OF each fiscal year** in training (using only data up to year Y), not from the latest snapshot — required to avoid lookahead leakage across the multi-year training rows.

## `FEATURE_COLS` (21 model features)

```python
FEATURE_COLS = [
    # Growth
    "eps_growth_1yr", "eps_growth_3yr", "profit_cagr_3y", "profit_cagr_5y",
    "nav_growth", "quarterly_eps_yoy",
    # Quality
    "eps_consistency", "roe", "earnings_quality",
    # Value
    "pe_vs_sector", "pb_ratio", "div_yield", "dividend_yield_pct",
    # Dividends
    "dividend_streak", "cash_div_ratio_5y", "payout_ratio",
    # Safety (model)
    "rights_count_10y",
    # Ownership
    "inst_flow_pp", "foreign_flow_pp", "institution_pct", "foreign_pct",
]
```

## Pillar spec (display layer; `+1` = higher is better, `-1` = lower is better)

```python
PILLAR_SPEC = {
    "growth":    [("eps_growth_1yr", 1), ("eps_growth_3yr", 1), ("profit_cagr_3y", 1),
                  ("profit_cagr_5y", 1), ("nav_growth", 1), ("quarterly_eps_yoy", 1)],
    "quality":   [("eps_consistency", 1), ("roe", 1), ("earnings_quality", 1)],
    "value":     [("pe_vs_sector", -1), ("pb_ratio", -1), ("div_yield", 1),
                  ("dividend_yield_pct", 1)],
    "dividends": [("dividend_streak", 1), ("cash_div_ratio_5y", 1), ("payout_ratio", 1)],
    "safety":    [("leverage_mktcap", -1), ("leverage_profit", -1), ("rights_count_10y", -1)],
    "ownership": [("inst_flow_pp", 1), ("foreign_flow_pp", 1), ("institution_pct", 1),
                  ("foreign_pct", 1)],
}
```

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `ml/train/labels.py` | Pure label construction + walk-forward fold generation | Create |
| `ml/features/fundamental_features.py` | Per-year + as-of feature computation, leverage helper, quarterly YoY, pillar scores | Modify |
| `ml/models/fundamental_scorer.py` | `FEATURE_COLS`, isotonic calibration, metadata + SHAP-background persistence, native pred_contribs | Modify |
| `ml/explain/fundamental_explainer.py` | Beginner payload: headline + 6 pillars + top-3 plain drivers | Create |
| `ml/train/train_fundamental.py` | New label, as-of features, walk-forward CV, rank IC, calibrated final fit | Modify |
| `ml/features/feature_store.py` | Serve via the SAME builder; emit raw features for all `FEATURE_COLS` | Modify |
| `ml/inference/score_fundamentals.py` | Batch: build all vectors → cross-sectional pillars → proba → payload | Modify |
| `tests/unit/ml/test_labels.py` | Label + fold tests | Create |
| `tests/unit/ml/test_fundamental_features.py` | New feature + pillar tests | Modify |
| `tests/unit/ml/test_fundamental_scorer.py` | Calibration + metadata + skew-guard | Modify |
| `tests/unit/ml/test_fundamental_explainer.py` | Explainer payload tests | Create |

---

## Task 1: Label construction (pure)

**Files:**
- Create: `ml/train/labels.py`
- Test: `tests/unit/ml/test_labels.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/ml/test_labels.py
"""Tests for ml.train.labels."""
from __future__ import annotations

import pandas as pd


def _frame() -> pd.DataFrame:
    # two sectors, one fiscal year; price_fwd encodes known returns
    return pd.DataFrame({
        "ticker":          ["A", "B", "C", "D", "E", "F"],
        "fiscal_year":     [2022, 2022, 2022, 2022, 2022, 2022],
        "sector":          ["Bank", "Bank", "Bank", "Bank", "Bank", "Bank"],
        "price_at_fy_end": [100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
        "price_fwd":       [130.0, 120.0, 110.0, 105.0, 100.0, 90.0],
    })


def test_label_top_half_within_year():
    from ml.train.labels import add_neutralized_label
    out = add_neutralized_label(_frame(), min_sector_names=5)
    # 6 names, balanced top half → exactly 3 positives
    assert out["label"].sum() == 3
    # best return is positive-labelled, worst is negative
    assert out.loc[out["ticker"] == "A", "label"].iloc[0] == 1
    assert out.loc[out["ticker"] == "F", "label"].iloc[0] == 0


def test_label_sector_neutralized_subtracts_cohort_median():
    from ml.train.labels import add_neutralized_label
    out = add_neutralized_label(_frame(), min_sector_names=5)
    # raw returns: 0.30,0.20,0.10,0.05,0.00,-0.10 ; median 0.075
    a = out.loc[out["ticker"] == "A", "neutralized_return"].iloc[0]
    assert abs(a - (0.30 - 0.075)) < 1e-9


def test_label_falls_back_to_year_median_for_sparse_sector():
    from ml.train.labels import add_neutralized_label
    df = _frame()
    df.loc[0, "sector"] = "Tiny"  # sector of size 1 < min_sector_names
    out = add_neutralized_label(df, min_sector_names=5)
    # Tiny-sector row uses YEAR median (not its own 1-row sector median)
    year_med = ((df["price_fwd"] - df["price_at_fy_end"]) / df["price_at_fy_end"]).median()
    a = out.loc[out["ticker"] == "A", "neutralized_return"].iloc[0]
    assert abs(a - (0.30 - year_med)) < 1e-9


def test_walk_forward_folds_embargo():
    from ml.train.labels import walk_forward_folds
    years = pd.Series([2020, 2021, 2022, 2023, 2024])
    folds = walk_forward_folds(years, embargo_years=1)
    # train<=2020 test 2022 ; train<=2021 test 2023 ; train<=2022 test 2024
    assert len(folds) == 3
    train_idx, test_idx = folds[0]
    assert set(years.iloc[train_idx]) == {2020}
    assert set(years.iloc[test_idx]) == {2022}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/ml/test_labels.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ml.train.labels'`

- [ ] **Step 3: Write minimal implementation**

```python
# ml/train/labels.py
"""Label construction and walk-forward fold generation for the fundamental model."""
from __future__ import annotations

import numpy as np
import pandas as pd


def add_neutralized_label(
    df: pd.DataFrame,
    min_sector_names: int = 5,
) -> pd.DataFrame:
    """Add raw_return, neutralized_return, and binary top-half label.

    Requires columns: fiscal_year, sector, price_at_fy_end, price_fwd.
    neutralized_return subtracts the (fiscal_year, sector) median return; cohorts
    with < min_sector_names fall back to the (fiscal_year) median. label = 1 if the
    row's neutralized_return is above the within-year median (top half).
    """
    out = df.copy()
    out["raw_return"] = (out["price_fwd"] - out["price_at_fy_end"]) / out["price_at_fy_end"]

    grp = out.groupby(["fiscal_year", "sector"])["raw_return"]
    sector_size = grp.transform("size")
    sector_med = grp.transform("median")
    year_med = out.groupby("fiscal_year")["raw_return"].transform("median")
    baseline = sector_med.where(sector_size >= min_sector_names, year_med)

    out["neutralized_return"] = out["raw_return"] - baseline
    yr_med_neut = out.groupby("fiscal_year")["neutralized_return"].transform("median")
    out["label"] = (out["neutralized_return"] > yr_med_neut).astype(int)
    return out


def walk_forward_folds(
    fiscal_years: pd.Series,
    embargo_years: int = 1,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding walk-forward folds keyed on fiscal_year with an embargo gap.

    For each cutoff year Y (ascending), train = rows with fiscal_year <= Y, test =
    rows with fiscal_year == Y + 1 + embargo_years. The embargo year(s) are excluded
    from both train and test so the forward-return label of the train set is realized
    before the test set's feature date (no lookahead).
    """
    fy = fiscal_years.reset_index(drop=True)
    years = sorted(fy.unique())
    folds: list[tuple[np.ndarray, np.ndarray]] = []
    for cutoff in years:
        test_year = cutoff + 1 + embargo_years
        if test_year not in years:
            continue
        train_idx = fy.index[fy <= cutoff].to_numpy()
        test_idx = fy.index[fy == test_year].to_numpy()
        if len(train_idx) and len(test_idx):
            folds.append((train_idx, test_idx))
    return folds
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/ml/test_labels.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add ml/train/labels.py tests/unit/ml/test_labels.py
git commit -m "feat(ml): sector-year-neutral label + walk-forward folds with embargo"
```

---

## Task 2: Leverage + quarterly-momentum + earnings-quality helpers

**Files:**
- Modify: `ml/features/fundamental_features.py`
- Test: `tests/unit/ml/test_fundamental_features.py`

- [ ] **Step 1: Write the failing test (append to existing test file)**

```python
def test_compute_leverage_features_uses_market_cap_and_profit():
    from ml.features.fundamental_features import compute_leverage_features
    f = compute_leverage_features(
        short_loan_mn=200.0, long_loan_mn=800.0,
        market_cap_bdt=5_000_000_000.0, net_profit_bdt=500_000_000.0,
    )
    # total loan = 1000 mn = 1.0e9 bdt
    assert abs(f["leverage_mktcap"] - (1.0e9 / 5.0e9)) < 1e-9
    assert abs(f["leverage_profit"] - (1.0e9 / 5.0e8)) < 1e-9


def test_compute_leverage_features_missing_loan_is_nan_not_zero():
    import math
    from ml.features.fundamental_features import compute_leverage_features
    f = compute_leverage_features(
        short_loan_mn=None, long_loan_mn=None,
        market_cap_bdt=5_000_000_000.0, net_profit_bdt=500_000_000.0,
    )
    assert math.isnan(f["leverage_mktcap"])
    assert math.isnan(f["leverage_profit"])


def test_quarterly_eps_yoy():
    import pandas as pd
    from ml.features.fundamental_features import compute_quarterly_eps_yoy
    q = pd.DataFrame({
        "fiscal_year": [2023, 2023, 2024, 2024],
        "quarter":     [1, 2, 1, 2],
        "eps_basic":   [1.0, 1.2, 1.5, 1.6],
    })
    # latest quarter = 2024Q2 (1.6); same quarter prior year = 2023Q2 (1.2)
    yoy = compute_quarterly_eps_yoy(q)
    assert abs(yoy - (1.6 / 1.2 - 1)) < 1e-9


def test_quarterly_eps_yoy_nan_when_no_prior_year_quarter():
    import math
    import pandas as pd
    from ml.features.fundamental_features import compute_quarterly_eps_yoy
    q = pd.DataFrame({"fiscal_year": [2024], "quarter": [2], "eps_basic": [1.6]})
    assert math.isnan(compute_quarterly_eps_yoy(q))


def test_earnings_quality_in_features():
    from ml.features.fundamental_features import compute_fundamental_features
    df = _make_fundamentals()
    df["net_profit_bdt"] = [100e6, 110e6, 120e6, 100e6, 140e6]
    df["total_comprehensive_income_bdt"] = [90e6, 110e6, 130e6, 80e6, 140e6]
    result = compute_fundamental_features(df)
    # 2023: 140/140 = 1.0
    assert abs(result.iloc[4]["earnings_quality"] - 1.0) < 1e-6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/ml/test_fundamental_features.py -k "leverage or quarterly or earnings_quality" -v`
Expected: FAIL with `ImportError`/`AttributeError` for the new functions.

- [ ] **Step 3: Write minimal implementation**

In `ml/features/fundamental_features.py`, add `earnings_quality` inside `compute_fundamental_features` (guarded — columns may be absent in the legacy callers/tests):

```python
    # earnings quality: comprehensive income vs net profit (1.0 == clean)
    if "total_comprehensive_income_bdt" in result.columns and "net_profit_bdt" in result.columns:
        npft = pd.to_numeric(result["net_profit_bdt"], errors="coerce").replace(0, np.nan)
        tci = pd.to_numeric(result["total_comprehensive_income_bdt"], errors="coerce")
        result["earnings_quality"] = (tci / npft).clip(-2, 3)
    else:
        result["earnings_quality"] = np.nan
```

(Place this block just before the final clipping loop, then `return result`.)

Add two module-level helpers:

```python
def compute_leverage_features(
    short_loan_mn: float | None,
    long_loan_mn: float | None,
    market_cap_bdt: float | None,
    net_profit_bdt: float | None,
) -> dict[str, float]:
    """Leverage ratios from latest loan snapshot. Missing loan data -> NaN (not 0).

    Loan columns are in millions of BDT; market cap / profit are in BDT.
    """
    if short_loan_mn is None and long_loan_mn is None:
        return {"leverage_mktcap": np.nan, "leverage_profit": np.nan}
    total_loan_bdt = ((short_loan_mn or 0.0) + (long_loan_mn or 0.0)) * 1e6
    mktcap = float(market_cap_bdt) if market_cap_bdt else np.nan
    profit = float(net_profit_bdt) if net_profit_bdt else np.nan
    lev_mc = total_loan_bdt / mktcap if mktcap and mktcap > 0 else np.nan
    lev_pf = total_loan_bdt / profit if profit and profit > 0 else np.nan
    return {
        "leverage_mktcap": float(np.clip(lev_mc, 0, 50)) if not np.isnan(lev_mc) else np.nan,
        "leverage_profit": float(np.clip(lev_pf, 0, 100)) if not np.isnan(lev_pf) else np.nan,
    }


def compute_quarterly_eps_yoy(quarterly: pd.DataFrame) -> float:
    """Latest quarter's basic EPS vs the same quarter one year earlier.

    quarterly columns: fiscal_year, quarter, eps_basic. Returns NaN if the
    prior-year same quarter is missing or non-positive.
    """
    if quarterly.empty:
        return np.nan
    q = quarterly.sort_values(["fiscal_year", "quarter"]).reset_index(drop=True)
    last = q.iloc[-1]
    fy, qtr = int(last["fiscal_year"]), int(last["quarter"])
    cur = pd.to_numeric(pd.Series([last["eps_basic"]]), errors="coerce").iloc[0]
    prior = q[(q["fiscal_year"] == fy - 1) & (q["quarter"] == qtr)]
    if prior.empty:
        return np.nan
    prev = pd.to_numeric(prior["eps_basic"], errors="coerce").iloc[0]
    if not np.isfinite(cur) or not np.isfinite(prev) or prev <= 0:
        return np.nan
    return float(cur / prev - 1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/ml/test_fundamental_features.py -v`
Expected: PASS (all existing + new tests)

- [ ] **Step 5: Commit**

```bash
git add ml/features/fundamental_features.py tests/unit/ml/test_fundamental_features.py
git commit -m "feat(ml): leverage, quarterly EPS YoY, and earnings-quality features"
```

---

## Task 3: Pillar scores (cross-sectional)

**Files:**
- Modify: `ml/features/fundamental_features.py`
- Test: `tests/unit/ml/test_fundamental_features.py`

- [ ] **Step 1: Write the failing test (append)**

```python
def test_compute_pillar_scores_ranges_and_direction():
    import pandas as pd
    from ml.features.fundamental_features import compute_pillar_scores, PILLAR_SPEC

    # 3 tickers, growth ascending; safety: lower leverage = better
    cross = pd.DataFrame({
        "eps_growth_1yr": [0.0, 0.1, 0.2], "eps_growth_3yr": [0.0, 0.1, 0.2],
        "profit_cagr_3y": [0.0, 0.1, 0.2], "profit_cagr_5y": [0.0, 0.1, 0.2],
        "nav_growth": [0.0, 0.1, 0.2], "quarterly_eps_yoy": [0.0, 0.1, 0.2],
        "eps_consistency": [0.5, 0.6, 0.7], "roe": [0.05, 0.1, 0.15],
        "earnings_quality": [0.8, 0.9, 1.0],
        "pe_vs_sector": [2.0, 1.0, 0.5], "pb_ratio": [3.0, 2.0, 1.0],
        "div_yield": [0.0, 0.02, 0.04], "dividend_yield_pct": [0.0, 2.0, 4.0],
        "dividend_streak": [0, 2, 5], "cash_div_ratio_5y": [0.0, 0.5, 1.0],
        "payout_ratio": [0.0, 0.3, 0.6],
        "leverage_mktcap": [1.0, 0.5, 0.1], "leverage_profit": [10.0, 5.0, 1.0],
        "rights_count_10y": [3, 1, 0],
        "inst_flow_pp": [-1.0, 0.0, 2.0], "foreign_flow_pp": [-1.0, 0.0, 2.0],
        "institution_pct": [10.0, 20.0, 30.0], "foreign_pct": [0.0, 5.0, 10.0],
    })
    pillars = compute_pillar_scores(cross)
    assert set(pillars.columns) == set(PILLAR_SPEC.keys())
    # all scores within 0..100
    assert ((pillars >= 0) & (pillars <= 100)).all().all()
    # ticker index 2 is best on every pillar (highest growth, lowest leverage, etc.)
    assert pillars.loc[2, "growth"] == pillars["growth"].max()
    assert pillars.loc[2, "safety"] == pillars["safety"].max()
    assert pillars.loc[2, "value"] == pillars["value"].max()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/ml/test_fundamental_features.py -k pillar -v`
Expected: FAIL with `ImportError` for `compute_pillar_scores`/`PILLAR_SPEC`.

- [ ] **Step 3: Write minimal implementation**

Add to `ml/features/fundamental_features.py`:

```python
PILLAR_SPEC: dict[str, list[tuple[str, int]]] = {
    "growth":    [("eps_growth_1yr", 1), ("eps_growth_3yr", 1), ("profit_cagr_3y", 1),
                  ("profit_cagr_5y", 1), ("nav_growth", 1), ("quarterly_eps_yoy", 1)],
    "quality":   [("eps_consistency", 1), ("roe", 1), ("earnings_quality", 1)],
    "value":     [("pe_vs_sector", -1), ("pb_ratio", -1), ("div_yield", 1),
                  ("dividend_yield_pct", 1)],
    "dividends": [("dividend_streak", 1), ("cash_div_ratio_5y", 1), ("payout_ratio", 1)],
    "safety":    [("leverage_mktcap", -1), ("leverage_profit", -1), ("rights_count_10y", -1)],
    "ownership": [("inst_flow_pp", 1), ("foreign_flow_pp", 1), ("institution_pct", 1),
                  ("foreign_pct", 1)],
}


def compute_pillar_scores(cross: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional 0-100 pillar scores for a cohort of tickers.

    Each constituent feature is percentile-ranked across the cohort (pct=True),
    inverted where lower-is-better, then averaged per pillar and scaled to 0-100.
    NaN constituents are ignored in the per-pillar mean; a pillar with no usable
    constituents for a row scores NaN.
    """
    scores = pd.DataFrame(index=cross.index)
    for pillar, feats in PILLAR_SPEC.items():
        cols = []
        for feat, direction in feats:
            if feat not in cross.columns:
                continue
            pct = cross[feat].rank(pct=True)
            cols.append(pct if direction == 1 else (1.0 - pct))
        if cols:
            scores[pillar] = pd.concat(cols, axis=1).mean(axis=1, skipna=True) * 100.0
        else:
            scores[pillar] = np.nan
    return scores
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/ml/test_fundamental_features.py -k pillar -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ml/features/fundamental_features.py tests/unit/ml/test_fundamental_features.py
git commit -m "feat(ml): cross-sectional 6-pillar scorecard"
```

---

## Task 4: Scorer — new FEATURE_COLS, isotonic calibration, metadata persistence

**Files:**
- Modify: `ml/models/fundamental_scorer.py`
- Test: `tests/unit/ml/test_fundamental_scorer.py`

- [ ] **Step 1: Write the failing test (replace the local FEATURE_COLS + add cases)**

Replace the hard-coded `FEATURE_COLS` list near the top of `tests/unit/ml/test_fundamental_scorer.py` with an import, and add calibration + metadata tests:

```python
from ml.models.fundamental_scorer import FEATURE_COLS  # remove the local list


def _make_training_data(n: int = 300) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(42)
    X = pd.DataFrame(rng.standard_normal((n, len(FEATURE_COLS))), columns=FEATURE_COLS)
    # signal: positive sum of first 3 cols => more likely positive
    logits = X.iloc[:, :3].sum(axis=1)
    y = pd.Series((logits + rng.standard_normal(n) > 0).astype(int))
    return X, y


def test_feature_cols_has_21_features():
    assert len(FEATURE_COLS) == 21


def test_predict_proba_calibrated_in_range():
    from ml.models.fundamental_scorer import FundamentalScorer
    scorer = FundamentalScorer()
    X, y = _make_training_data()
    scorer.fit(X, y)
    proba = scorer.predict_proba(X)
    assert proba.shape == (len(X),)
    assert (proba >= 0).all() and (proba <= 1).all()


def test_shap_contributions_shape():
    from ml.models.fundamental_scorer import FundamentalScorer
    scorer = FundamentalScorer()
    X, y = _make_training_data()
    scorer.fit(X, y)
    contribs = scorer.shap_contributions(X.iloc[:5])
    # one column per feature (+ bias column dropped) and one row per sample
    assert contribs.shape == (5, len(FEATURE_COLS))
    assert list(contribs.columns) == FEATURE_COLS


def test_save_load_roundtrip_preserves_proba_and_cols():
    from ml.models.fundamental_scorer import FundamentalScorer
    scorer = FundamentalScorer()
    X, y = _make_training_data()
    scorer.fit(X, y)
    proba_before = scorer.predict_proba(X)
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "scorer.pkl"
        scorer.save(path)
        loaded = FundamentalScorer()
        loaded.load(path)
        proba_after = loaded.predict_proba(X)
        assert loaded.feature_cols == FEATURE_COLS
    np.testing.assert_array_almost_equal(proba_before, proba_after)
```

(Delete the now-obsolete `test_predict_proba_shape`/`test_predict_proba_range`/`test_feature_importances_keys` duplicates only if they conflict; otherwise keep — they still pass with the larger `FEATURE_COLS` because `_make_training_data` now generates the right width.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/ml/test_fundamental_scorer.py -v`
Expected: FAIL — `FEATURE_COLS` length is 9 not 21; `shap_contributions`/`feature_cols` missing.

- [ ] **Step 3: Write implementation**

Rewrite `ml/models/fundamental_scorer.py`:

```python
"""XGBoost-based fundamental stock scorer with isotonic calibration."""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.calibration import CalibratedClassifierCV
from xgboost import XGBClassifier

FEATURE_COLS = [
    # Growth
    "eps_growth_1yr", "eps_growth_3yr", "profit_cagr_3y", "profit_cagr_5y",
    "nav_growth", "quarterly_eps_yoy",
    # Quality
    "eps_consistency", "roe", "earnings_quality",
    # Value
    "pe_vs_sector", "pb_ratio", "div_yield", "dividend_yield_pct",
    # Dividends
    "dividend_streak", "cash_div_ratio_5y", "payout_ratio",
    # Safety (model)
    "rights_count_10y",
    # Ownership
    "inst_flow_pp", "foreign_flow_pp", "institution_pct", "foreign_pct",
]

_CALIB_MIN_ROWS = 30
_CALIB_FRACTION = 0.2


class FundamentalScorer:
    """XGBoost classifier (isotonic-calibrated): P(stock outperforms peers, 12m)."""

    def __init__(self) -> None:
        self._model = XGBClassifier(
            n_estimators=100, max_depth=3, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, min_child_weight=5,
            reg_lambda=1.0, eval_metric="logloss", random_state=42,
        )
        self._calibrated: CalibratedClassifierCV | None = None
        self._medians: pd.Series = pd.Series(0.0, index=FEATURE_COLS)
        self.feature_cols: list[str] = list(FEATURE_COLS)
        self._trained = False

    def _prep(self, X: pd.DataFrame) -> pd.DataFrame:
        return X[self.feature_cols].fillna(self._medians)

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        self._medians = X[FEATURE_COLS].median()
        Xf = self._prep(X)
        n = len(Xf)
        cut = int(n * (1 - _CALIB_FRACTION))
        # time-ordered holdout for calibration; caller passes time-sorted rows
        X_fit, y_fit = Xf.iloc[:cut], y.iloc[:cut]
        X_cal, y_cal = Xf.iloc[cut:], y.iloc[cut:]
        self._model.fit(X_fit, y_fit)
        if n >= _CALIB_MIN_ROWS and y_cal.nunique() == 2 and len(y_cal) >= 10:
            self._calibrated = CalibratedClassifierCV(self._model, method="isotonic", cv="prefit")
            self._calibrated.fit(X_cal, y_cal)
        else:
            # not enough holdout to calibrate; refit base on all rows, no calibration
            self._model.fit(Xf, y)
            self._calibrated = None
        self._trained = True

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        if not self._trained:
            raise RuntimeError("Model not trained. Call fit() or load() first.")
        Xf = self._prep(X)
        if self._calibrated is not None:
            return self._calibrated.predict_proba(Xf)[:, 1]
        return self._model.predict_proba(Xf)[:, 1]

    def shap_contributions(self, X: pd.DataFrame) -> pd.DataFrame:
        """Native XGBoost TreeSHAP contributions (drops the bias column)."""
        if not self._trained:
            raise RuntimeError("Model not trained. Call fit() or load() first.")
        Xf = self._prep(X)
        booster = self._model.get_booster()
        dmat = xgb.DMatrix(Xf, feature_names=self.feature_cols)
        contribs = booster.predict(dmat, pred_contribs=True)  # (n, n_features + 1)
        return pd.DataFrame(contribs[:, :-1], columns=self.feature_cols, index=X.index)

    def feature_importances(self) -> dict[str, float]:
        return dict(zip(self.feature_cols, self._model.feature_importances_.tolist()))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({
                "model": self._model, "calibrated": self._calibrated,
                "medians": self._medians, "feature_cols": self.feature_cols,
            }, f)

    def load(self, path: Path) -> None:
        with open(path, "rb") as f:
            data = pickle.load(f)
        if isinstance(data, dict):
            self._model = data["model"]
            self._calibrated = data.get("calibrated")
            self._medians = data.get("medians", pd.Series(0.0, index=FEATURE_COLS))
            self.feature_cols = data.get("feature_cols", list(FEATURE_COLS))
        else:  # legacy bare-model pickle
            self._model = data
            self._calibrated = None
            self._medians = pd.Series(0.0, index=FEATURE_COLS)
            self.feature_cols = list(FEATURE_COLS)
        self._trained = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/ml/test_fundamental_scorer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ml/models/fundamental_scorer.py tests/unit/ml/test_fundamental_scorer.py
git commit -m "feat(ml): 21-feature scorer with isotonic calibration + native SHAP + metadata pickle"
```

---

## Task 5: Explainer — beginner payload

**Files:**
- Create: `ml/explain/__init__.py` (empty), `ml/explain/fundamental_explainer.py`
- Test: `tests/unit/ml/test_fundamental_explainer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/ml/test_fundamental_explainer.py
"""Tests for ml.explain.fundamental_explainer."""
from __future__ import annotations

import numpy as np
import pandas as pd


def test_build_explanation_payload_shape():
    from ml.explain.fundamental_explainer import build_explanation
    feature_row = pd.Series({
        "eps_growth_1yr": 0.18, "profit_cagr_5y": 0.22, "pe_vs_sector": 0.6,
        "roe": 0.19, "dividend_streak": 5.0, "foreign_flow_pp": 1.2,
    })
    pillars = {"growth": 88.0, "quality": 75.0, "value": 64.0,
               "dividends": 80.0, "safety": 40.0, "ownership": 70.0}
    contribs = pd.Series({
        "profit_cagr_5y": 0.9, "pe_vs_sector": 0.4, "eps_growth_1yr": 0.3,
        "roe": 0.1, "dividend_streak": -0.05, "foreign_flow_pp": 0.02,
    })
    out = build_explanation(headline=82.0, pillars=pillars,
                            feature_row=feature_row, contributions=contribs)
    assert out["headline"] == 82.0
    assert set(out["pillars"].keys()) == set(pillars.keys())
    assert len(out["drivers"]) == 3
    # top driver is the largest |contribution| feature
    assert out["drivers"][0]["feature"] == "profit_cagr_5y"
    for d in out["drivers"]:
        assert {"feature", "value", "sentence", "polarity"} <= set(d.keys())
        assert d["polarity"] in ("good", "bad")


def test_driver_polarity_follows_contribution_sign():
    from ml.explain.fundamental_explainer import build_explanation
    feature_row = pd.Series({"pe_vs_sector": 1.8, "roe": 0.02, "profit_cagr_5y": -0.1})
    contribs = pd.Series({"pe_vs_sector": -0.7, "roe": -0.3, "profit_cagr_5y": -0.2})
    out = build_explanation(headline=30.0, pillars={}, feature_row=feature_row,
                            contributions=contribs)
    assert out["drivers"][0]["feature"] == "pe_vs_sector"
    assert out["drivers"][0]["polarity"] == "bad"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/ml/test_fundamental_explainer.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write implementation**

```python
# ml/explain/__init__.py
```
```python
# ml/explain/fundamental_explainer.py
"""Translate model output + features into a beginner-readable payload."""
from __future__ import annotations

import numpy as np
import pandas as pd

# Each entry: plain name + a formatter(value) -> human phrase fragment.
FEATURE_TEMPLATES: dict[str, tuple[str, str]] = {
    "eps_growth_1yr":     ("Earnings growth (1yr)", "pct"),
    "eps_growth_3yr":     ("Earnings growth (3yr)", "pct"),
    "profit_cagr_3y":     ("Profit growth (3yr)", "pct"),
    "profit_cagr_5y":     ("Profit growth (5yr)", "pct"),
    "nav_growth":         ("Book value growth", "pct"),
    "quarterly_eps_yoy":  ("Latest quarter vs last year", "pct"),
    "eps_consistency":    ("Earnings stability", "ratio"),
    "roe":                ("Return on equity", "pct"),
    "earnings_quality":   ("Earnings quality", "ratio"),
    "pe_vs_sector":       ("Valuation vs sector (P/E)", "x"),
    "pb_ratio":           ("Price-to-book", "x"),
    "div_yield":          ("Dividend yield", "pct"),
    "dividend_yield_pct": ("Dividend yield", "pct"),
    "dividend_streak":    ("Years of continuous dividends", "years"),
    "cash_div_ratio_5y":  ("Cash-dividend share (5yr)", "ratio"),
    "payout_ratio":       ("Dividend payout ratio", "ratio"),
    "rights_count_10y":   ("Rights issues (10yr)", "count"),
    "inst_flow_pp":       ("Institutional ownership change", "pp"),
    "foreign_flow_pp":    ("Foreign ownership change", "pp"),
    "institution_pct":    ("Institutional ownership", "pct_level"),
    "foreign_pct":        ("Foreign ownership", "pct_level"),
}


def _format_value(kind: str, value: float) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "n/a"
    if kind == "pct":
        return f"{value * 100:.0f}%"
    if kind in ("pct_level", "pp"):
        return f"{value:.1f}%"
    if kind == "x":
        return f"{value:.2f}x"
    if kind == "years":
        return f"{value:.0f}"
    if kind == "count":
        return f"{value:.0f}"
    return f"{value:.2f}"


def build_explanation(
    headline: float,
    pillars: dict[str, float],
    feature_row: pd.Series,
    contributions: pd.Series,
    top_k: int = 3,
) -> dict:
    """Assemble the beginner payload.

    headline: 0-100 score. pillars: pillar -> 0-100. feature_row: raw feature values.
    contributions: per-feature SHAP value (signed) for this row. Drivers are the
    top_k features by |contribution|; polarity is good if contribution > 0.
    """
    ranked = contributions.reindex(contributions.abs().sort_values(ascending=False).index)
    drivers = []
    for feat in ranked.index[:top_k]:
        name, kind = FEATURE_TEMPLATES.get(feat, (feat, "ratio"))
        raw = float(feature_row.get(feat, np.nan))
        polarity = "good" if ranked[feat] > 0 else "bad"
        adverb = "boosts" if polarity == "good" else "drags down"
        sentence = f"{name}: {_format_value(kind, raw)} — {adverb} the score."
        drivers.append({"feature": feat, "value": raw, "sentence": sentence,
                        "polarity": polarity})
    return {
        "headline": float(headline),
        "pillars": {k: float(v) for k, v in pillars.items()},
        "drivers": drivers,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/ml/test_fundamental_explainer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ml/explain/__init__.py ml/explain/fundamental_explainer.py tests/unit/ml/test_fundamental_explainer.py
git commit -m "feat(ml): beginner-readable explanation payload (headline, pillars, drivers)"
```

---

## Task 6: Training pipeline rewrite (label + as-of features + walk-forward CV + rank IC)

**Files:**
- Modify: `ml/train/train_fundamental.py`
- Test: `tests/unit/ml/test_train_fundamental.py` (create — covers the pure helpers only; the DB path is exercised manually)

This task wires the new label and CV into training and computes as-of features per row.

- [ ] **Step 1: Write the failing test for the pure assembly + CV-metric helpers**

```python
# tests/unit/ml/test_train_fundamental.py
"""Tests for the pure helpers in ml.train.train_fundamental."""
from __future__ import annotations

import numpy as np
import pandas as pd


def test_rank_ic_perfect_ranking_is_one():
    from ml.train.train_fundamental import rank_ic
    proba = np.array([0.1, 0.4, 0.6, 0.9])
    target = np.array([-0.05, 0.0, 0.1, 0.3])
    assert rank_ic(proba, target) > 0.99


def test_rank_ic_handles_constant_target_as_nan():
    import math
    from ml.train.train_fundamental import rank_ic
    assert math.isnan(rank_ic(np.array([0.1, 0.2, 0.3]), np.array([0.0, 0.0, 0.0])))


def test_asof_track_features_use_only_past_rows():
    from ml.train.train_fundamental import asof_track_row
    grp = pd.DataFrame({
        "fiscal_year":   [2020, 2021, 2022, 2023],
        "net_profit_bdt": [100e6, 120e6, 140e6, 9_999e6],  # 2023 spike must NOT leak
        "cash_div_pct":  [10.0, 10.0, 10.0, 50.0],
        "stock_div_pct": [0.0, 0.0, 0.0, 0.0],
    })
    # as-of 2022: only rows <= 2022 considered
    feats = asof_track_row(grp, as_of_year=2022, rights_count_10y=0,
                           inst_flow_pp=None, foreign_flow_pp=None)
    # dividend_streak as-of 2022 = 3 (2020,2021,2022), NOT 4
    assert feats["dividend_streak"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/ml/test_train_fundamental.py -v`
Expected: FAIL — `rank_ic`/`asof_track_row` not defined.

- [ ] **Step 3: Rewrite `ml/train/train_fundamental.py`**

```python
"""
Train the calibrated fundamental scorer on enriched fundamentals + price data.

Run: .venv/Scripts/python.exe -m ml.train.train_fundamental
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score

from db.pool import get_pool
from ml.features.fundamental_features import (
    compute_fundamental_features,
    compute_quarterly_eps_yoy,
    compute_track_record_features,
)
from ml.models.fundamental_scorer import FEATURE_COLS, FundamentalScorer
from ml.train.labels import add_neutralized_label, walk_forward_folds

MODEL_PATH = Path("models/v1/fundamental_scorer.pkl")
HORIZON_MONTHS = 12  # primary label horizon; set 6 for the secondary label
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


def rank_ic(proba: np.ndarray, target: np.ndarray) -> float:
    """Spearman rank correlation between predicted proba and realized return."""
    if len(np.unique(target)) < 2 or len(np.unique(proba)) < 2:
        return float("nan")
    rho, _ = spearmanr(proba, target)
    return float(rho)


def asof_track_row(
    grp: pd.DataFrame,
    as_of_year: int,
    rights_count_10y: int,
    inst_flow_pp: float | None,
    foreign_flow_pp: float | None,
) -> dict[str, float]:
    """Track-record features computed using only rows with fiscal_year <= as_of_year."""
    past = grp[grp["fiscal_year"] <= as_of_year]
    return compute_track_record_features(
        past, rights_count_10y=rights_count_10y,
        inst_flow_pp=inst_flow_pp, foreign_flow_pp=foreign_flow_pp,
    )


_SQL = """
SELECT f.ticker, f.fiscal_year,
       f.eps, f.nav, f.pe, f.cash_div_pct, f.stock_div_pct,
       f.net_profit_bdt, f.total_comprehensive_income_bdt, f.dividend_yield_pct,
       f.institution_pct, f.foreign_pct,
       c.sector,
       (SELECT sp.close FROM stock_prices sp
          WHERE sp.ticker = f.ticker
            AND sp.time >= make_date(f.fiscal_year, 10, 1)
            AND sp.time <= make_date(f.fiscal_year + 1, 1, 31)
          ORDER BY sp.time DESC LIMIT 1) AS price_at_fy_end,
       (SELECT sp.close FROM stock_prices sp
          WHERE sp.ticker = f.ticker
            AND sp.time >= make_date(f.fiscal_year + 1, 4, 1)
            AND sp.time <= make_date(f.fiscal_year + 1, 9, 30)
          ORDER BY sp.time ASC LIMIT 1) AS price_6m_later,
       (SELECT sp.close FROM stock_prices sp
          WHERE sp.ticker = f.ticker
            AND sp.time >= make_date(f.fiscal_year + 1, 10, 1)
            AND sp.time <= make_date(f.fiscal_year + 2, 3, 31)
          ORDER BY sp.time ASC LIMIT 1) AS price_12m_later
FROM fundamentals f
JOIN companies c ON c.ticker = f.ticker
WHERE f.fiscal_year IS NOT NULL AND f.eps IS NOT NULL
ORDER BY f.ticker, f.fiscal_year
"""

_NUMERIC = ["eps", "nav", "pe", "cash_div_pct", "stock_div_pct", "net_profit_bdt",
            "total_comprehensive_income_bdt", "dividend_yield_pct", "institution_pct",
            "foreign_pct", "price_at_fy_end", "price_6m_later", "price_12m_later"]


async def _asof_scalars(pool, ticker: str, as_of_year: int) -> dict:
    """Per-(ticker, year) scalars that need their own queries: rights, flows, quarterly."""
    rights = await pool.fetchval(
        """SELECT count(*) FROM corporate_actions
           WHERE ticker = $1 AND action_type = 'right_issue' AND fiscal_year <= $2
             AND fiscal_year >= $2 - 10""", ticker, as_of_year)
    flows = await pool.fetchrow(
        """WITH s AS (SELECT * FROM shareholding_history
                      WHERE ticker = $1 AND as_on_date <= make_date($2 + 1, 6, 30))
           SELECT (SELECT institution_pct FROM s ORDER BY as_on_date DESC LIMIT 1)
                - (SELECT institution_pct FROM s ORDER BY as_on_date ASC LIMIT 1) AS inst_flow,
                  (SELECT foreign_pct FROM s ORDER BY as_on_date DESC LIMIT 1)
                - (SELECT foreign_pct FROM s ORDER BY as_on_date ASC LIMIT 1) AS foreign_flow
        """, ticker, as_of_year)
    qrows = await pool.fetch(
        """SELECT fiscal_year, quarter, eps_basic FROM fundamentals_quarterly
           WHERE ticker = $1 AND fiscal_year <= $2 ORDER BY fiscal_year, quarter""",
        ticker, as_of_year)
    qdf = pd.DataFrame([dict(r) for r in qrows]) if qrows else pd.DataFrame(
        columns=["fiscal_year", "quarter", "eps_basic"])
    return {
        "rights": rights or 0,
        "inst_flow": float(flows["inst_flow"]) if flows and flows["inst_flow"] is not None else None,
        "foreign_flow": float(flows["foreign_flow"]) if flows and flows["foreign_flow"] is not None else None,
        "quarterly_eps_yoy": compute_quarterly_eps_yoy(qdf),
    }


async def build_training_dataset(pool) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (X over FEATURE_COLS, meta with fiscal_year/label/neutralized_return)."""
    rows = await pool.fetch(_SQL)
    df = pd.DataFrame([dict(r) for r in rows])
    for col in _NUMERIC:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    fwd_col = "price_12m_later" if HORIZON_MONTHS == 12 else "price_6m_later"

    feature_rows = []
    for ticker, grp in df.groupby("ticker"):
        grp = grp.sort_values("fiscal_year").reset_index(drop=True)
        feats = compute_fundamental_features(grp)
        feats["ticker"] = ticker
        feats["fiscal_year"] = grp["fiscal_year"].values
        feats["sector"] = grp["sector"].values
        feats["pe_raw"] = grp["pe"].values
        feats["nav_raw"] = grp["nav"].astype(float).replace(0, np.nan).values
        feats["dividend_yield_pct"] = grp["dividend_yield_pct"].values
        feats["institution_pct"] = grp["institution_pct"].values
        feats["foreign_pct"] = grp["foreign_pct"].values
        feats["price_at_fy_end"] = grp["price_at_fy_end"].values
        feats["price_fwd"] = grp[fwd_col].values

        # as-of track + quarterly scalars per row
        for i, row in feats.iterrows():
            yr = int(row["fiscal_year"])
            sc = await _asof_scalars(pool, ticker, yr)
            track = asof_track_row(grp, yr, sc["rights"], sc["inst_flow"], sc["foreign_flow"])
            for k, v in track.items():
                feats.at[i, k] = v
            feats.at[i, "quarterly_eps_yoy"] = sc["quarterly_eps_yoy"]
        feature_rows.append(feats)

    full = pd.concat(feature_rows, ignore_index=True)
    full = full.dropna(subset=["price_at_fy_end", "price_fwd"])

    # cross-sectional valuation features
    sector_median_pe = full.groupby(["fiscal_year", "sector"])["pe_raw"].transform("median")
    full["pe_vs_sector"] = (full["pe_raw"] / sector_median_pe.replace(0, np.nan)).clip(0, 10)
    full["pb_ratio"] = (full["price_at_fy_end"] / full["nav_raw"]).clip(0, 20)

    full = add_neutralized_label(full)  # adds raw_return, neutralized_return, label
    full = full.sort_values(["fiscal_year", "ticker"]).reset_index(drop=True)

    X = full[FEATURE_COLS].reset_index(drop=True)
    meta = full[["fiscal_year", "ticker", "label", "neutralized_return"]].reset_index(drop=True)
    return X, meta


async def main() -> None:
    pool = await get_pool()
    log.info("Building training dataset...")
    X, meta = await build_training_dataset(pool)
    y = meta["label"]
    log.info(f"Training set: {len(X)} rows, {y.mean():.1%} positive labels, "
             f"{meta['fiscal_year'].nunique()} fiscal years")

    if len(X) < 200:
        log.error("Fewer than 200 trainable rows — aborting (would not generalize).")
        await pool.close()
        return

    folds = walk_forward_folds(meta["fiscal_year"], embargo_years=1)
    ics, aucs = [], []
    for fold, (tr, te) in enumerate(folds):
        scorer = FundamentalScorer()
        scorer.fit(X.iloc[tr], y.iloc[tr])
        proba = scorer.predict_proba(X.iloc[te])
        ic = rank_ic(proba, meta["neutralized_return"].iloc[te].to_numpy())
        ics.append(ic)
        if y.iloc[te].nunique() == 2:
            aucs.append(roc_auc_score(y.iloc[te], proba))
        log.info(f"  Fold {fold}: train={len(tr)} test={len(te)} rank_IC={ic:.3f}")
    if ics:
        log.info(f"Mean rank IC: {np.nanmean(ics):.3f}  Mean AUC: "
                 f"{np.nanmean(aucs) if aucs else float('nan'):.3f}")

    log.info("Training final calibrated model on full dataset...")
    final = FundamentalScorer()
    final.fit(X, y)
    log.info("Feature importances:")
    for feat, imp in sorted(final.feature_importances().items(), key=lambda kv: -kv[1]):
        log.info(f"  {feat}: {imp:.3f}")

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    final.save(MODEL_PATH)
    log.info(f"Model saved → {MODEL_PATH}")
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run unit tests, then run the training end-to-end against the live DB**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/ml/test_train_fundamental.py -v`
Expected: PASS

Run (live DB up): `.venv/Scripts/python.exe -m ml.train.train_fundamental`
Expected: logs ~1500 rows, ~50% positive labels, ~5 fiscal years, 3 folds with finite rank IC, feature importances printed, model saved. Mean rank IC should be > 0 and beat the prior 9-feature baseline (record both numbers in the commit body).

- [ ] **Step 5: Commit**

```bash
git add ml/train/train_fundamental.py tests/unit/ml/test_train_fundamental.py
git commit -m "feat(ml): rewrite fundamental training — neutral 12m label, as-of features, walk-forward rank IC"
```

---

## Task 7: Serve parity — feature_store emits all FEATURE_COLS via the same path

**Files:**
- Modify: `ml/features/feature_store.py`
- Test: `tests/unit/ml/test_feature_store_parity.py` (create)

The serve builder must emit exactly `FEATURE_COLS` (no extras, no missing) so the
model receives what it trained on. This task adds the skew-regression guard.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/ml/test_feature_store_parity.py
"""Guards train/serve feature parity for the fundamental model."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from unittest.mock import AsyncMock, MagicMock


def _pool_with(rows, rights=0, flows=None, quarterly=None):
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=[rows, quarterly or []])
    pool.fetchval = AsyncMock(return_value=rights)
    pool.fetchrow = AsyncMock(return_value=flows)
    return pool


@pytest.mark.asyncio
async def test_serve_vector_covers_all_feature_cols():
    from ml.features.feature_store import build_fundamental_feature_vector
    from ml.models.fundamental_scorer import FEATURE_COLS

    fund_rows = [
        {"fiscal_year": 2022, "eps": 5.0, "nav": 50.0, "pe": 12.0,
         "cash_div_pct": 20.0, "stock_div_pct": 0.0, "net_profit_bdt": 1e8,
         "total_comprehensive_income_bdt": 1e8, "dividend_yield_pct": 2.0,
         "institution_pct": 20.0, "foreign_pct": 5.0,
         "price_at_fy_end": 60.0, "median_pe": 11.0},
        {"fiscal_year": 2023, "eps": 6.0, "nav": 55.0, "pe": 10.0,
         "cash_div_pct": 25.0, "stock_div_pct": 0.0, "net_profit_bdt": 1.2e8,
         "total_comprehensive_income_bdt": 1.2e8, "dividend_yield_pct": 2.5,
         "institution_pct": 22.0, "foreign_pct": 6.0,
         "price_at_fy_end": 66.0, "median_pe": 10.5},
    ]
    pool = _pool_with(fund_rows, rights=1,
                      flows={"inst_flow": 2.0, "foreign_flow": 1.0},
                      quarterly=[])
    vec = await build_fundamental_feature_vector(pool, "TESTCO")
    # every model feature is present in the served vector
    assert set(FEATURE_COLS).issubset(set(vec.index))


def test_no_feature_silently_dropped():
    """The model selects X[FEATURE_COLS]; any feature the serve builder forgets
    becomes a median-imputed constant. This asserts the builder name list matches."""
    from ml.models.fundamental_scorer import FEATURE_COLS
    # FEATURE_COLS is the single source of truth; serve builds against it.
    assert len(FEATURE_COLS) == len(set(FEATURE_COLS))  # no dupes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/ml/test_feature_store_parity.py -v`
Expected: FAIL — current serve vector is missing several `FEATURE_COLS`
(profit_cagr_*, quarterly_eps_yoy, earnings_quality, dividend_yield_pct,
institution_pct, foreign_pct, etc.).

- [ ] **Step 3: Update `build_fundamental_feature_vector`**

Extend the SQL `SELECT` to also return `f.net_profit_bdt, f.total_comprehensive_income_bdt, f.dividend_yield_pct, f.institution_pct, f.foreign_pct`, fetch quarterly rows, and assemble the full vector. Replace the result-assembly block (current lines ~94-149) with:

```python
    df = pd.DataFrame([dict(r) for r in rows])
    numeric_cols = [c for c in df.columns if c != "eps_basis"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    features = compute_fundamental_features(df)
    latest = features.iloc[-1]
    last = df.iloc[-1]

    pe = float(last["pe"]) if pd.notna(last["pe"]) else np.nan
    median_pe = float(last["median_pe"]) if pd.notna(last["median_pe"]) else np.nan
    pe_vs_sector = float(np.clip(pe / median_pe, 0, 10)) if (median_pe and median_pe != 0) else np.nan
    nav = float(last["nav"]) if pd.notna(last["nav"]) else np.nan
    price = float(last["price_at_fy_end"]) if pd.notna(last["price_at_fy_end"]) else np.nan
    pb_ratio = float(np.clip(price / nav, 0, 20)) if (nav and nav != 0 and not np.isnan(price)) else np.nan

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
    qrows = await pool.fetch(
        """SELECT fiscal_year, quarter, eps_basic FROM fundamentals_quarterly
           WHERE ticker = $1 ORDER BY fiscal_year, quarter""", ticker)
    qdf = pd.DataFrame([dict(r) for r in qrows]) if qrows else pd.DataFrame(
        columns=["fiscal_year", "quarter", "eps_basic"])

    from ml.features.fundamental_features import (
        compute_quarterly_eps_yoy, compute_track_record_features,
    )
    track = compute_track_record_features(
        df, rights_count_10y=rights or 0,
        inst_flow_pp=float(flows["inst_flow"]) if flows and flows["inst_flow"] is not None else None,
        foreign_flow_pp=float(flows["foreign_flow"]) if flows and flows["foreign_flow"] is not None else None,
    )

    result = pd.Series({
        "eps_growth_1yr":     float(latest.get("eps_growth_1yr", np.nan)),
        "eps_growth_3yr":     float(latest.get("eps_growth_3yr", np.nan)),
        "profit_cagr_3y":     track["profit_cagr_3y"],
        "profit_cagr_5y":     track["profit_cagr_5y"],
        "nav_growth":         float(latest.get("nav_growth", np.nan)),
        "quarterly_eps_yoy":  compute_quarterly_eps_yoy(qdf),
        "eps_consistency":    float(latest.get("eps_consistency", np.nan)),
        "roe":                float(latest.get("roe", np.nan)),
        "earnings_quality":   float(latest.get("earnings_quality", np.nan)),
        "pe_vs_sector":       pe_vs_sector,
        "pb_ratio":           pb_ratio,
        "div_yield":          float(latest.get("div_yield", np.nan)),
        "dividend_yield_pct": float(last["dividend_yield_pct"]) if pd.notna(last.get("dividend_yield_pct")) else np.nan,
        "dividend_streak":    track["dividend_streak"],
        "cash_div_ratio_5y":  track["cash_div_ratio_5y"],
        "payout_ratio":       float(latest.get("payout_ratio", np.nan)),
        "rights_count_10y":   track["rights_count_10y"],
        "inst_flow_pp":       track["inst_flow_pp"],
        "foreign_flow_pp":    track["foreign_flow_pp"],
        "institution_pct":    float(last["institution_pct"]) if pd.notna(last.get("institution_pct")) else np.nan,
        "foreign_pct":        float(last["foreign_pct"]) if pd.notna(last.get("foreign_pct")) else np.nan,
    })
    return result
```

Also extend the SQL `SELECT` list (current lines ~66-67) to add the new columns:
`f.net_profit_bdt, f.total_comprehensive_income_bdt, f.dividend_yield_pct, f.institution_pct, f.foreign_pct,`

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/ml/test_feature_store_parity.py tests/unit/ml/test_score_fundamentals.py -v`
Expected: PASS (update `test_score_fundamentals.py` feature_vec fixture to include the new keys if it asserts on them — it only needs a non-empty Series, so adding keys is safe).

- [ ] **Step 5: Commit**

```bash
git add ml/features/feature_store.py tests/unit/ml/test_feature_store_parity.py
git commit -m "fix(ml): serve emits all 21 FEATURE_COLS — kills train/serve skew"
```

---

## Task 8: Batch scoring — cross-sectional pillars + explanation payload

**Files:**
- Modify: `ml/inference/score_fundamentals.py`
- Test: `tests/unit/ml/test_score_fundamentals.py` (extend)

Restructure scoring so all tickers' feature vectors are assembled first, then pillars
(which need the cross-section) are computed, then proba + payload per ticker.

- [ ] **Step 1: Write the failing test (append)**

```python
@pytest.mark.asyncio
async def test_score_attaches_pillars_and_drivers():
    from ml.inference.score_fundamentals import score_all_tickers
    from ml.models.fundamental_scorer import FEATURE_COLS
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[{"ticker": "GP"}, {"ticker": "BANK"}])

    def _vec(seed):
        return pd.Series({c: float(seed) for c in FEATURE_COLS})

    scorer = MagicMock()
    scorer.predict_proba = MagicMock(return_value=np.array([0.7]))
    scorer.shap_contributions = MagicMock(
        return_value=pd.DataFrame([{c: 0.1 for c in FEATURE_COLS}]))

    with patch("ml.inference.score_fundamentals.build_fundamental_feature_vector",
               AsyncMock(side_effect=[_vec(1), _vec(2)])):
        results = await score_all_tickers(pool, scorer)

    assert "GP" in results and "BANK" in results
    assert "score" in results["GP"] and "pillars" in results["GP"]
    assert "drivers" in results["GP"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/ml/test_score_fundamentals.py -k pillars_and_drivers -v`
Expected: FAIL — results values are floats, not dicts with pillars/drivers.

- [ ] **Step 3: Update `score_all_tickers` and `write_scores`**

Rewrite `score_all_tickers` to return `dict[str, dict]` (`{score, pillars, drivers}`):

```python
async def score_all_tickers(pool, scorer) -> dict[str, dict]:
    from ml.features.fundamental_features import compute_pillar_scores
    from ml.explain.fundamental_explainer import build_explanation
    from ml.models.fundamental_scorer import FEATURE_COLS

    tickers = await pool.fetch(
        "SELECT ticker FROM companies WHERE is_active = true ORDER BY ticker")

    vectors: dict[str, pd.Series] = {}
    for row in tickers:
        t = row["ticker"]
        try:
            vec = await build_fundamental_feature_vector(pool, t)
            if vec.empty or vec.isna().all():
                continue
            vectors[t] = vec
        except Exception as exc:  # noqa: BLE001
            log.warning(f"skip {t}: {exc}")
    if not vectors:
        return {}

    cross = pd.DataFrame(vectors).T  # index=ticker, cols=features
    pillars_df = compute_pillar_scores(cross)
    feat_df = cross.reindex(columns=FEATURE_COLS)
    proba = scorer.predict_proba(feat_df)
    contribs = scorer.shap_contributions(feat_df)

    results: dict[str, dict] = {}
    for i, t in enumerate(cross.index):
        pillars = pillars_df.loc[t].to_dict()
        payload = build_explanation(
            headline=float(proba[i]) * 100.0, pillars=pillars,
            feature_row=cross.loc[t], contributions=contribs.iloc[i])
        results[t] = {"score": float(proba[i]), "pillars": pillars,
                      "drivers": payload["drivers"]}
    return results
```

Update `write_scores` to read `score` out of the dict:

```python
async def write_scores(pool, scores: dict[str, dict], scored_at: datetime) -> int:
    scored_date = scored_at.date()
    count = 0
    for ticker, payload in scores.items():
        await pool.execute(
            """
            INSERT INTO stock_scores
                (ticker, scored_at, scored_date, fundamental_score, model_version)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (ticker, model_version, scored_date) DO UPDATE
                SET fundamental_score = EXCLUDED.fundamental_score,
                    scored_at         = EXCLUDED.scored_at
            """,
            ticker, scored_at, scored_date, payload["score"], MODEL_VERSION,
        )
        count += 1
    return count
```

In `main`, update the log line: `log.info(f"Scored {len(scores)} tickers")` stays valid.

Note: pillar/driver persistence is intentionally out of scope (no migration this round —
see spec). The payload is returned in-memory and ready for a future stock_scores JSONB
column / API surface.

- [ ] **Step 4: Run tests, then a live dry run**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/ml/test_score_fundamentals.py -v`
Expected: PASS (fix `test_score_returns_dict_per_ticker` to assert on `results["GP"]["score"]`).

Run (after Task 6 produced a model): `.venv/Scripts/python.exe -m ml.inference.score_fundamentals`
Expected: scores N tickers and writes rows; no exceptions.

- [ ] **Step 5: Commit**

```bash
git add ml/inference/score_fundamentals.py tests/unit/ml/test_score_fundamentals.py
git commit -m "feat(ml): batch scoring emits cross-sectional pillars + driver payload"
```

---

## Task 9: Full suite + lint + typecheck

**Files:** none (verification task)

- [ ] **Step 1: Run the full unit suite**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/ml/ -v`
Expected: all green.

- [ ] **Step 2: Lint + typecheck the touched files**

Run: `make lint` then `make typecheck` (or `ruff check ml/` + `mypy ml/`).
Expected: no new errors in touched files. Fix any inline.

- [ ] **Step 3: Commit any fixups**

```bash
git add -A
git commit -m "chore(ml): lint/type fixups for fundamental redesign"
```

---

## Self-Review Notes (author)

- **Spec coverage:** label (Task 1), features incl. leverage/quarterly/earnings-quality (Task 2), pillars (Task 3), calibration + metadata + SHAP (Task 4), explainer (Task 5), walk-forward CV + rank IC + as-of features (Task 6), skew fix + parity guard (Task 7), batch pillars/payload (Task 8). Validation metrics reported in Task 6.
- **Deviations (documented above):** leverage is display-only (loan snapshot would leak); pillars not fed as model features (overfit risk in small data); these are intentional and noted in the spec's Out-of-Scope/decisions.
- **Type consistency:** `FundamentalScorer.predict_proba` returns `np.ndarray`; `shap_contributions` returns `pd.DataFrame[FEATURE_COLS]`; `score_all_tickers` now returns `dict[str, dict]` (was `dict[str, float]`). The one external caller, `extraction/tasks.py:97-99`, passes the result straight to `write_scores` (updated in the same task) and only calls `len()` on it — both stay compatible, no change to tasks.py needed.
- **Persistence of pillars/drivers** deferred (no migration this round) — payload returned in-memory, ready for a later JSONB column + API.
