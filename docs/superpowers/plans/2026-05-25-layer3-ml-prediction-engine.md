# Layer 3 — ML Prediction Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a nightly ML inference pipeline that produces price direction predictions (LSTM), fundamental scores (XGBoost), intrinsic value estimates (DCF), and a composite 0–100 Stock Health Score for all 406 active DSE tickers.

**Architecture:** Feature engineering reads from TimescaleDB (stock_prices, fundamentals, macro_indicators, sector_pe) and outputs clean DataFrames. Model classes (XGBoost + PyTorch LSTM) are trained offline and saved to `models/v1/`. A nightly APScheduler job loads saved models, runs inference on all active tickers, and writes predictions + scores to `ml_predictions` and `stock_scores` tables. Accuracy monitoring evaluates predictions once their horizon passes.

**Tech Stack:** scikit-learn 1.5, xgboost 2.0, ta 0.11 (technical indicators), torch 2.3 (CPU), joblib 1.4, asyncpg (DB), APScheduler (scheduler)

**Data situation (2026-05-25):**
- XGBoost: ready to train now — 1,945 fundamental rows × 406 tickers
- LSTM: thin data — ~475 rows/ticker full OHLCV (2024–2026 only); treat as v0 baseline
- DCF: deterministic — no training, just EPS + growth rate math

---

## File Map

```
ml/
  __init__.py
  features/
    __init__.py
    price_features.py          # compute_price_features(df) → DataFrame
    fundamental_features.py    # compute_fundamental_features(df) → DataFrame
    macro_features.py          # compute_macro_features(df) → DataFrame
    feature_store.py           # build_price_feature_matrix(), build_fundamental_feature_vector()
  models/
    __init__.py
    fundamental_scorer.py      # FundamentalScorer (XGBoost wrapper)
    lstm_predictor.py          # LSTMPredictor (PyTorch)
  train/
    __init__.py
    train_fundamental.py       # CLI: python -m ml.train.train_fundamental
    train_lstm.py              # CLI: python -m ml.train.train_lstm
  inference/
    __init__.py
    score_fundamentals.py      # run_fundamental_scoring() → writes stock_scores
    predict_prices.py          # run_price_prediction() → writes ml_predictions
  valuation/
    __init__.py
    dcf.py                     # DCFCalculator.calculate()
    monte_carlo.py             # MonteCarloSimulator.simulate()
  scoring/
    __init__.py
    health_score.py            # compute_health_scores() → writes stock_scores
  monitoring/
    __init__.py
    accuracy_report.py         # populate_outcomes(), generate_report()

db/migrations/
  018_ml_predictions.sql
  019_stock_scores.sql
  020_prediction_outcomes.sql

tests/unit/ml/
  __init__.py
  test_price_features.py
  test_fundamental_features.py
  test_macro_features.py
  test_feature_store.py
  test_fundamental_scorer.py
  test_lstm_predictor.py
  test_dcf.py
  test_monte_carlo.py
  test_health_score.py
  test_accuracy_report.py
```

**Modified:**
- `pyproject.toml` — add ML deps + `ml` package
- `extraction/scheduler.py` — add `job_nightly_ml()` + `job_quarterly_retrain()`

---

## Task 1: ML Dependencies + Package Skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `ml/__init__.py`, `ml/features/__init__.py`, `ml/models/__init__.py`, `ml/train/__init__.py`, `ml/inference/__init__.py`, `ml/valuation/__init__.py`, `ml/scoring/__init__.py`, `ml/monitoring/__init__.py`
- Create: `tests/unit/ml/__init__.py`

- [ ] **Step 1: Add ML deps to pyproject.toml**

In `pyproject.toml`, add to the `dependencies` list after the `"rapidfuzz>=3.9.0"` line:

```toml
    # ML
    "scikit-learn>=1.5",
    "xgboost>=2.0",
    "ta>=0.11",
    "torch>=2.3",
    "joblib>=1.4",
```

Also add `"ml"` to `[tool.hatch.build.targets.wheel] packages`:
```toml
packages = ["extraction", "db", "mgmt", "ml"]
```

- [ ] **Step 2: Install deps**

```bash
pip install scikit-learn>=1.5 xgboost>=2.0 ta>=0.11 torch>=2.3 joblib>=1.4
```

> **Note (Windows CPU-only PyTorch):** If `pip install torch` pulls a CUDA build and fails, use:
> `pip install torch --index-url https://download.pytorch.org/whl/cpu`

- [ ] **Step 3: Create package init files**

Create `ml/__init__.py` (empty):
```python
```

Repeat for `ml/features/__init__.py`, `ml/models/__init__.py`, `ml/train/__init__.py`, `ml/inference/__init__.py`, `ml/valuation/__init__.py`, `ml/scoring/__init__.py`, `ml/monitoring/__init__.py`, `tests/unit/ml/__init__.py` — all empty files.

- [ ] **Step 4: Verify imports work**

```bash
python -c "import sklearn; import xgboost; import ta; import torch; print('ML deps OK')"
```

Expected: `ML deps OK`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml ml/ tests/unit/ml/
git commit -m "feat(ml): add ML deps and package skeleton"
```

---

## Task 2: DB Migrations (018–020)

**Files:**
- Create: `db/migrations/018_ml_predictions.sql`
- Create: `db/migrations/019_stock_scores.sql`
- Create: `db/migrations/020_prediction_outcomes.sql`

- [ ] **Step 1: Create 018_ml_predictions.sql**

```sql
-- LSTM price direction predictions per ticker per horizon

CREATE TABLE IF NOT EXISTS ml_predictions (
    id              BIGSERIAL       PRIMARY KEY,
    ticker          TEXT            NOT NULL REFERENCES companies (ticker),
    predicted_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    horizon_days    SMALLINT        NOT NULL,
    direction       TEXT            NOT NULL,
    confidence      NUMERIC(6, 4)   NOT NULL,
    target_price    NUMERIC(12, 4),
    model_version   TEXT            NOT NULL DEFAULT 'lstm_v0',
    CONSTRAINT chk_confidence CHECK (confidence BETWEEN 0 AND 1),
    CONSTRAINT chk_horizon    CHECK (horizon_days IN (5, 10, 20)),
    CONSTRAINT chk_direction  CHECK (direction IN ('up', 'down'))
);

CREATE INDEX IF NOT EXISTS idx_ml_predictions_ticker_horizon
    ON ml_predictions (ticker, horizon_days, predicted_at DESC);
CREATE INDEX IF NOT EXISTS idx_ml_predictions_predicted_at
    ON ml_predictions (predicted_at DESC);
```

- [ ] **Step 2: Create 019_stock_scores.sql**

```sql
-- Composite stock health scores (0–100)

CREATE TABLE IF NOT EXISTS stock_scores (
    id                  BIGSERIAL       PRIMARY KEY,
    ticker              TEXT            NOT NULL REFERENCES companies (ticker),
    scored_at           TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    health_score        NUMERIC(6, 2),
    fundamental_score   NUMERIC(6, 4),
    momentum_score      NUMERIC(6, 4),
    valuation_score     NUMERIC(6, 4),
    sentiment_score     NUMERIC(6, 4),
    model_version       TEXT            NOT NULL DEFAULT 'v1',
    CONSTRAINT chk_health CHECK (health_score BETWEEN 0 AND 100)
);

CREATE INDEX IF NOT EXISTS idx_stock_scores_ticker_scored
    ON stock_scores (ticker, scored_at DESC);
CREATE INDEX IF NOT EXISTS idx_stock_scores_health
    ON stock_scores (health_score DESC, scored_at DESC);
```

- [ ] **Step 3: Create 020_prediction_outcomes.sql**

```sql
-- Tracks actual vs predicted direction once horizon passes

CREATE TABLE IF NOT EXISTS prediction_outcomes (
    id                  BIGSERIAL       PRIMARY KEY,
    prediction_id       BIGINT          NOT NULL REFERENCES ml_predictions (id),
    ticker              TEXT            NOT NULL,
    horizon_days        SMALLINT        NOT NULL,
    predicted_at        TIMESTAMPTZ     NOT NULL,
    predicted_direction TEXT            NOT NULL,
    confidence          NUMERIC(6, 4)   NOT NULL,
    price_at_prediction NUMERIC(12, 4),
    price_at_horizon    NUMERIC(12, 4),
    return_pct          NUMERIC(8, 4),
    actual_direction    TEXT,
    correct             BOOLEAN,
    evaluated_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_outcomes_pending
    ON prediction_outcomes (predicted_at, correct) WHERE correct IS NULL;
CREATE INDEX IF NOT EXISTS idx_outcomes_evaluated
    ON prediction_outcomes (correct, horizon_days, evaluated_at DESC);
```

- [ ] **Step 4: Apply migrations**

```bash
docker compose exec mgmt_api python db/migrate.py
```

Expected output includes lines:
```
Applied: 018_ml_predictions.sql
Applied: 019_stock_scores.sql
Applied: 020_prediction_outcomes.sql
```

- [ ] **Step 5: Verify tables exist**

```bash
make db-shell
```
```sql
\dt ml_predictions
\dt stock_scores
\dt prediction_outcomes
\q
```

- [ ] **Step 6: Commit**

```bash
git add db/migrations/018_ml_predictions.sql db/migrations/019_stock_scores.sql db/migrations/020_prediction_outcomes.sql
git commit -m "feat(db): add ml_predictions, stock_scores, prediction_outcomes tables"
```

---

## Task 3: Price Features Module

**Files:**
- Create: `ml/features/price_features.py`
- Create: `tests/unit/ml/test_price_features.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/ml/test_price_features.py
import numpy as np
import pandas as pd
import pytest

REQUIRED_COLS = [
    "rsi_14", "macd_diff", "bb_pband", "atr_norm", "adx_14",
    "return_1d", "return_5d", "return_20d", "volume_zscore", "obv",
]


def _make_ohlcv(n: int = 70) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    price = 100 + rng.standard_normal(n).cumsum()
    price = np.abs(price) + 10  # ensure positive
    return pd.DataFrame(
        {
            "close": price,
            "high": price + rng.uniform(0.5, 2, n),
            "low": price - rng.uniform(0.5, 2, n),
            "volume": rng.integers(100_000, 1_000_000, n).astype(float),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="D"),
    )


def test_returns_required_columns():
    from ml.features.price_features import compute_price_features
    result = compute_price_features(_make_ohlcv())
    for col in REQUIRED_COLS:
        assert col in result.columns, f"missing column: {col}"


def test_row_count_preserved():
    from ml.features.price_features import compute_price_features
    df = _make_ohlcv(70)
    result = compute_price_features(df)
    assert len(result) == 70


def test_no_infinite_values():
    from ml.features.price_features import compute_price_features
    result = compute_price_features(_make_ohlcv())
    feat_cols = [c for c in REQUIRED_COLS if c in result.columns]
    assert not result[feat_cols].isin([np.inf, -np.inf]).any().any()


def test_rsi_range_0_to_100():
    from ml.features.price_features import compute_price_features
    result = compute_price_features(_make_ohlcv())
    valid = result["rsi_14"].dropna()
    assert (valid >= 0).all() and (valid <= 100).all()


def test_atr_norm_positive():
    from ml.features.price_features import compute_price_features
    result = compute_price_features(_make_ohlcv())
    valid = result["atr_norm"].dropna()
    assert (valid >= 0).all()
```

- [ ] **Step 2: Run tests — expect FAIL (module not found)**

```bash
pytest tests/unit/ml/test_price_features.py -v
```

Expected: `ModuleNotFoundError: No module named 'ml.features.price_features'`

- [ ] **Step 3: Implement price_features.py**

```python
# ml/features/price_features.py
"""Compute technical indicator features from OHLCV DataFrame."""
from __future__ import annotations

import numpy as np
import pandas as pd
import ta


def compute_price_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute technical indicators from OHLCV DataFrame.

    Args:
        df: DataFrame with columns [close, high, low, volume], DatetimeIndex.
            Minimum 26 rows recommended for indicator warmup.

    Returns:
        DataFrame with original columns plus feature columns.
        NaN values appear in early rows during indicator warmup — caller
        should forward-fill or drop as needed.
    """
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    result = df.copy()

    result["rsi_14"] = ta.momentum.RSIIndicator(close, window=14).rsi()

    macd = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
    result["macd_diff"] = macd.macd_diff()

    bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    result["bb_pband"] = bb.bollinger_pband()

    atr = ta.volatility.AverageTrueRange(high, low, close, window=14)
    result["atr_norm"] = atr.average_true_range() / close.replace(0, np.nan)

    result["adx_14"] = ta.trend.ADXIndicator(high, low, close, window=14).adx()

    result["return_1d"] = close.pct_change(1)
    result["return_5d"] = close.pct_change(5)
    result["return_20d"] = close.pct_change(20)

    vol_mean = volume.rolling(20).mean()
    vol_std = volume.rolling(20).std().replace(0, np.nan)
    result["volume_zscore"] = (volume - vol_mean) / vol_std

    obv = ta.volume.OnBalanceVolumeIndicator(close, volume).on_balance_volume()
    result["obv"] = obv.pct_change(1)

    return result
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/unit/ml/test_price_features.py -v
```

Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add ml/features/price_features.py tests/unit/ml/test_price_features.py
git commit -m "feat(ml): price features module (RSI, MACD, Bollinger, ATR, ADX, returns)"
```

---

## Task 4: Fundamental Features Module

**Files:**
- Create: `ml/features/fundamental_features.py`
- Create: `tests/unit/ml/test_fundamental_features.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/ml/test_fundamental_features.py
import numpy as np
import pandas as pd


def _make_fundamentals() -> pd.DataFrame:
    return pd.DataFrame({
        "fiscal_year": [2019, 2020, 2021, 2022, 2023],
        "eps":         [5.0,  5.5,  6.0,  5.8,  7.0],
        "nav":         [50.0, 53.0, 56.0, 54.0, 60.0],
        "pe":          [12.0, 11.0, 13.0, 12.5, 10.0],
        "cash_div_pct":[20.0, 20.0, 25.0, 20.0, 30.0],
        "stock_div_pct":[0.0,  0.0,  0.0,  5.0,  0.0],
    })


def test_eps_growth_1yr():
    from ml.features.fundamental_features import compute_fundamental_features
    result = compute_fundamental_features(_make_fundamentals())
    # 2020: (5.5 - 5.0) / 5.0 = 0.1
    assert abs(result.iloc[1]["eps_growth_1yr"] - 0.1) < 1e-6


def test_nav_growth_1yr():
    from ml.features.fundamental_features import compute_fundamental_features
    result = compute_fundamental_features(_make_fundamentals())
    # 2020: (53 - 50) / 50 = 0.06
    assert abs(result.iloc[1]["nav_growth"] - 0.06) < 1e-6


def test_eps_growth_3yr_cagr():
    from ml.features.fundamental_features import compute_fundamental_features
    result = compute_fundamental_features(_make_fundamentals())
    # 2022: (5.8 / 5.0)^(1/3) - 1 ≈ 0.05
    expected = (5.8 / 5.0) ** (1 / 3) - 1
    assert abs(result.iloc[3]["eps_growth_3yr"] - expected) < 1e-6


def test_eps_consistency_bounded_0_to_1():
    from ml.features.fundamental_features import compute_fundamental_features
    result = compute_fundamental_features(_make_fundamentals())
    valid = result["eps_consistency"].dropna()
    assert (valid >= 0).all() and (valid <= 1).all()


def test_handles_zero_eps_no_inf():
    from ml.features.fundamental_features import compute_fundamental_features
    df = _make_fundamentals()
    df.loc[2, "eps"] = 0.0
    result = compute_fundamental_features(df)
    assert not result["eps_growth_1yr"].isin([np.inf, -np.inf]).any()


def test_div_yield_nonnegative():
    from ml.features.fundamental_features import compute_fundamental_features
    result = compute_fundamental_features(_make_fundamentals())
    valid = result["div_yield"].dropna()
    assert (valid >= 0).all()
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/unit/ml/test_fundamental_features.py -v
```

- [ ] **Step 3: Implement fundamental_features.py**

```python
# ml/features/fundamental_features.py
"""Compute fundamental features from multi-year per-ticker fundamentals DataFrame."""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_fundamental_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute derived features from annual fundamentals.

    Args:
        df: DataFrame with columns [fiscal_year, eps, nav, pe, cash_div_pct,
            stock_div_pct]. One row per fiscal year, any order.

    Returns:
        DataFrame sorted by fiscal_year with added columns:
        eps_growth_1yr, eps_growth_3yr, nav_growth, div_yield, eps_consistency.
        pe_vs_sector is NOT computed here — it requires a sector_pe join.
    """
    result = df.sort_values("fiscal_year").copy().reset_index(drop=True)

    eps = result["eps"].astype(float).replace(0, np.nan)
    nav = result["nav"].astype(float).replace(0, np.nan)
    pe = result["pe"].astype(float).replace(0, np.nan)
    cash_div = result["cash_div_pct"].astype(float).fillna(0)

    result["eps_growth_1yr"] = eps.pct_change(1)
    result["eps_growth_3yr"] = (eps / eps.shift(3)).pow(1.0 / 3) - 1
    result["nav_growth"] = nav.pct_change(1)
    result["div_yield"] = (cash_div / 100.0) / pe

    eps_std = eps.rolling(3, min_periods=2).std()
    eps_mean = eps.rolling(3, min_periods=2).mean().abs().replace(0, np.nan)
    result["eps_consistency"] = 1.0 / (1.0 + (eps_std / eps_mean).fillna(1.0))

    # Clip extreme values (data errors in DSE filings)
    for col in ["eps_growth_1yr", "eps_growth_3yr", "nav_growth"]:
        result[col] = result[col].clip(-5, 5)

    return result
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/unit/ml/test_fundamental_features.py -v
```

Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add ml/features/fundamental_features.py tests/unit/ml/test_fundamental_features.py
git commit -m "feat(ml): fundamental features (EPS/NAV growth, consistency, div yield)"
```

---

## Task 5: Macro Features Module

**Files:**
- Create: `ml/features/macro_features.py`
- Create: `tests/unit/ml/test_macro_features.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/ml/test_macro_features.py
import pandas as pd
import numpy as np


def _make_macro(n: int = 30) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="ME")
    return pd.DataFrame({
        "usd_bdt":     110.0 + np.arange(n) * 0.1,
        "policy_rate": 8.0 + np.zeros(n),
        "cpi":         150.0 + np.arange(n) * 0.5,
    }, index=idx)


def test_usd_bdt_change_20d_present():
    from ml.features.macro_features import compute_macro_features
    result = compute_macro_features(_make_macro())
    assert "usd_bdt_change_20d" in result.columns


def test_policy_rate_delta_present():
    from ml.features.macro_features import compute_macro_features
    result = compute_macro_features(_make_macro())
    assert "policy_rate_delta" in result.columns


def test_cpi_trend_present():
    from ml.features.macro_features import compute_macro_features
    result = compute_macro_features(_make_macro())
    assert "cpi_trend" in result.columns


def test_handles_missing_columns_gracefully():
    from ml.features.macro_features import compute_macro_features
    df = pd.DataFrame({"usd_bdt": [110.0, 111.0]},
                      index=pd.date_range("2024-01-01", periods=2, freq="ME"))
    result = compute_macro_features(df)
    assert "usd_bdt_change_20d" in result.columns
    assert "policy_rate_delta" not in result.columns
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/unit/ml/test_macro_features.py -v
```

- [ ] **Step 3: Implement macro_features.py**

```python
# ml/features/macro_features.py
"""Compute derived macro features from macro_indicators pivot DataFrame."""
from __future__ import annotations

import pandas as pd


def compute_macro_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute derived macro features.

    Args:
        df: Wide DataFrame indexed by date. Columns may include any subset of
            [usd_bdt, policy_rate, cpi]. Missing columns are skipped.

    Returns:
        DataFrame with added derived columns for present columns only.
    """
    result = df.copy()

    if "usd_bdt" in df.columns:
        result["usd_bdt_change_20d"] = df["usd_bdt"].pct_change(20)

    if "policy_rate" in df.columns:
        result["policy_rate_delta"] = df["policy_rate"].diff(1)

    if "cpi" in df.columns:
        result["cpi_trend"] = df["cpi"].pct_change(12)

    return result
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/unit/ml/test_macro_features.py -v
```

Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add ml/features/macro_features.py tests/unit/ml/test_macro_features.py
git commit -m "feat(ml): macro features (USD/BDT change, policy rate delta, CPI trend)"
```

---

## Task 6: Feature Store

**Files:**
- Create: `ml/features/feature_store.py`
- Create: `tests/unit/ml/test_feature_store.py`

- [ ] **Step 1: Write failing tests (using mocked pool)**

```python
# tests/unit/ml/test_feature_store.py
import numpy as np
import pandas as pd
import pytest
from unittest.mock import AsyncMock, MagicMock


def _mock_pool_price(rows: list[dict]) -> MagicMock:
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=rows)
    return pool


def _make_price_records(n: int = 70) -> list[dict]:
    import datetime
    rng = np.random.default_rng(42)
    price = np.abs(100 + rng.standard_normal(n).cumsum()) + 10
    base = datetime.datetime(2024, 1, 1)
    return [
        {
            "time": base + datetime.timedelta(days=i),
            "close": float(price[i]),
            "high": float(price[i] + 1),
            "low": float(price[i] - 1),
            "volume": float(500_000),
        }
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_build_price_feature_matrix_returns_dataframe():
    from ml.features.feature_store import build_price_feature_matrix
    pool = _mock_pool_price(_make_price_records(70))
    result = await build_price_feature_matrix(pool, "GP", lookback_days=30)
    assert isinstance(result, pd.DataFrame)
    assert len(result) <= 30


@pytest.mark.asyncio
async def test_build_price_feature_matrix_empty_when_no_rows():
    from ml.features.feature_store import build_price_feature_matrix
    pool = _mock_pool_price([])
    result = await build_price_feature_matrix(pool, "GP", lookback_days=30)
    assert result.empty


@pytest.mark.asyncio
async def test_build_price_feature_matrix_no_nans_after_fill():
    from ml.features.feature_store import build_price_feature_matrix
    pool = _mock_pool_price(_make_price_records(70))
    result = await build_price_feature_matrix(pool, "GP", lookback_days=30)
    if not result.empty:
        assert not result.isnull().any().any()
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/unit/ml/test_feature_store.py -v
```

- [ ] **Step 3: Implement feature_store.py**

```python
# ml/features/feature_store.py
"""Fetch data from DB and assemble feature matrices for ML models."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd


async def build_price_feature_matrix(
    pool,
    ticker: str,
    lookback_days: int = 90,
) -> pd.DataFrame:
    """
    Fetch OHLCV from DB, compute price features, forward-fill NaN.

    Returns DataFrame with DatetimeIndex and price feature columns.
    Returns empty DataFrame if no price data found.
    """
    from ml.features.price_features import compute_price_features

    end = datetime.now(timezone.utc)
    # Fetch extra days for indicator warmup (ADX needs 28, MACD needs 26+9=35)
    start = end - timedelta(days=lookback_days + 40)

    rows = await pool.fetch(
        """
        SELECT time, close, high, low, volume
        FROM stock_prices
        WHERE ticker = $1 AND time >= $2 AND time <= $3
          AND close IS NOT NULL AND quality_flag != 'bad'
        ORDER BY time
        """,
        ticker, start, end,
    )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(list(rows), columns=["time", "close", "high", "low", "volume"])
    df = df.set_index("time").sort_index()
    df = df.astype(float)

    features = compute_price_features(df)
    features = features.ffill().fillna(0)
    return features.tail(lookback_days)


async def build_fundamental_feature_vector(pool, ticker: str) -> pd.Series:
    """
    Fetch multi-year fundamentals from DB and return latest feature vector.

    Returns Series with keys: eps_growth_1yr, eps_growth_3yr, nav_growth,
    div_yield, eps_consistency, pe_vs_sector. Missing values are NaN.
    Returns empty Series if no fundamental data found.
    """
    from ml.features.fundamental_features import compute_fundamental_features

    rows = await pool.fetch(
        """
        SELECT f.fiscal_year, f.eps, f.nav, f.pe, f.cash_div_pct, f.stock_div_pct,
               (
                   SELECT sp.pe FROM sector_pe sp
                   JOIN companies c ON c.sector = sp.sector
                   WHERE c.ticker = f.ticker
                   ORDER BY sp.fetched_at DESC LIMIT 1
               ) AS sector_pe
        FROM fundamentals f
        WHERE f.ticker = $1 AND f.fiscal_year IS NOT NULL
        ORDER BY f.fiscal_year
        """,
        ticker,
    )

    if not rows:
        return pd.Series(dtype=float)

    df = pd.DataFrame(
        list(rows),
        columns=["fiscal_year", "eps", "nav", "pe", "cash_div_pct", "stock_div_pct", "sector_pe"],
    )
    for col in ["eps", "nav", "pe", "cash_div_pct", "stock_div_pct", "sector_pe"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    features = compute_fundamental_features(df)
    latest = features.iloc[-1]

    sector_pe = float(df["sector_pe"].iloc[-1]) if pd.notna(df["sector_pe"].iloc[-1]) else np.nan
    pe = float(df["pe"].iloc[-1]) if pd.notna(df["pe"].iloc[-1]) else np.nan
    pe_vs_sector = pe / sector_pe if (sector_pe and sector_pe != 0) else np.nan

    return pd.Series({
        "eps_growth_1yr": float(latest.get("eps_growth_1yr", np.nan)),
        "eps_growth_3yr": float(latest.get("eps_growth_3yr", np.nan)),
        "nav_growth":     float(latest.get("nav_growth", np.nan)),
        "div_yield":      float(latest.get("div_yield", np.nan)),
        "eps_consistency":float(latest.get("eps_consistency", np.nan)),
        "pe_vs_sector":   pe_vs_sector,
    })
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/unit/ml/test_feature_store.py -v
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add ml/features/feature_store.py tests/unit/ml/test_feature_store.py
git commit -m "feat(ml): feature store — async DB fetch + feature assembly"
```

---

## Task 7: FundamentalScorer Model Class

**Files:**
- Create: `ml/models/fundamental_scorer.py`
- Create: `tests/unit/ml/test_fundamental_scorer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/ml/test_fundamental_scorer.py
import numpy as np
import pandas as pd
import pytest
from pathlib import Path
import tempfile


FEATURE_COLS = [
    "eps_growth_1yr", "eps_growth_3yr", "nav_growth",
    "pe_vs_sector", "div_yield", "eps_consistency",
]


def _make_training_data(n: int = 100) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(42)
    X = pd.DataFrame(rng.standard_normal((n, len(FEATURE_COLS))), columns=FEATURE_COLS)
    y = pd.Series((rng.random(n) > 0.5).astype(int))
    return X, y


def test_predict_proba_shape():
    from ml.models.fundamental_scorer import FundamentalScorer
    scorer = FundamentalScorer()
    X, y = _make_training_data()
    scorer.fit(X, y)
    proba = scorer.predict_proba(X)
    assert proba.shape == (len(X),)


def test_predict_proba_range():
    from ml.models.fundamental_scorer import FundamentalScorer
    scorer = FundamentalScorer()
    X, y = _make_training_data()
    scorer.fit(X, y)
    proba = scorer.predict_proba(X)
    assert (proba >= 0).all() and (proba <= 1).all()


def test_predict_before_fit_raises():
    from ml.models.fundamental_scorer import FundamentalScorer
    scorer = FundamentalScorer()
    X, _ = _make_training_data(10)
    with pytest.raises(RuntimeError, match="not trained"):
        scorer.predict_proba(X)


def test_save_load_roundtrip():
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

    np.testing.assert_array_almost_equal(proba_before, proba_after)


def test_feature_importances_keys():
    from ml.models.fundamental_scorer import FundamentalScorer
    scorer = FundamentalScorer()
    X, y = _make_training_data()
    scorer.fit(X, y)
    imps = scorer.feature_importances()
    assert set(imps.keys()) == set(FEATURE_COLS)


def test_handles_nan_features():
    from ml.models.fundamental_scorer import FundamentalScorer
    scorer = FundamentalScorer()
    X, y = _make_training_data()
    scorer.fit(X, y)
    X_nan = X.copy()
    X_nan.iloc[0, 0] = np.nan
    proba = scorer.predict_proba(X_nan)
    assert proba.shape == (len(X_nan),)
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/unit/ml/test_fundamental_scorer.py -v
```

- [ ] **Step 3: Implement fundamental_scorer.py**

```python
# ml/models/fundamental_scorer.py
"""XGBoost-based fundamental stock scorer."""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from xgboost import XGBClassifier

FEATURE_COLS = [
    "eps_growth_1yr",
    "eps_growth_3yr",
    "nav_growth",
    "pe_vs_sector",
    "div_yield",
    "eps_consistency",
]


class FundamentalScorer:
    """XGBoost classifier: P(stock outperforms peers over next 6 months)."""

    def __init__(self) -> None:
        self._model = XGBClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="logloss",
            random_state=42,
        )
        self._trained = False

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        self._model.fit(X[FEATURE_COLS].fillna(0), y)
        self._trained = True

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        if not self._trained:
            raise RuntimeError("Model not trained. Call fit() or load() first.")
        return self._model.predict_proba(X[FEATURE_COLS].fillna(0))[:, 1]

    def feature_importances(self) -> dict[str, float]:
        return dict(zip(FEATURE_COLS, self._model.feature_importances_.tolist()))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self._model, f)

    def load(self, path: Path) -> None:
        with open(path, "rb") as f:
            self._model = pickle.load(f)
        self._trained = True
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/unit/ml/test_fundamental_scorer.py -v
```

Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add ml/models/fundamental_scorer.py tests/unit/ml/test_fundamental_scorer.py
git commit -m "feat(ml): FundamentalScorer XGBoost model class with save/load"
```

---

## Task 8: Train XGBoost Fundamental Scorer

**Files:**
- Create: `ml/train/train_fundamental.py`
- Create: `models/v1/` directory (already gitignored)

- [ ] **Step 1: Create train_fundamental.py**

```python
# ml/train/train_fundamental.py
"""
Train XGBoost fundamental scorer on historical fundamentals + price data.

Run: python -m ml.train.train_fundamental
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import TimeSeriesSplit

from db.pool import get_pool
from ml.features.fundamental_features import compute_fundamental_features
from ml.models.fundamental_scorer import FEATURE_COLS, FundamentalScorer

MODEL_PATH = Path("models/v1/fundamental_scorer.pkl")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def build_training_dataset(pool) -> tuple[pd.DataFrame, pd.Series]:
    """
    Build feature matrix + binary labels from DB.

    Label = 1 if stock's close price 6 months after fiscal year end is higher
    than at fiscal year end (absolute return positive, cross-sectionally neutral).
    """
    rows = await pool.fetch(
        """
        SELECT f.ticker, f.fiscal_year,
               f.eps, f.nav, f.pe, f.cash_div_pct, f.stock_div_pct,
               (
                   SELECT sp2.pe FROM sector_pe sp2
                   JOIN companies c2 ON c2.sector = sp2.sector
                   WHERE c2.ticker = f.ticker
                     AND sp2.fetched_at <= make_date(f.fiscal_year, 12, 31)
                   ORDER BY sp2.fetched_at DESC LIMIT 1
               ) AS sector_pe,
               (
                   SELECT sp3.close FROM stock_prices sp3
                   WHERE sp3.ticker = f.ticker
                     AND sp3.time >= make_date(f.fiscal_year, 10, 1)
                     AND sp3.time <= make_date(f.fiscal_year + 1, 1, 31)
                   ORDER BY sp3.time DESC LIMIT 1
               ) AS price_at_fy_end,
               (
                   SELECT sp4.close FROM stock_prices sp4
                   WHERE sp4.ticker = f.ticker
                     AND sp4.time >= make_date(f.fiscal_year + 1, 4, 1)
                     AND sp4.time <= make_date(f.fiscal_year + 1, 9, 30)
                   ORDER BY sp4.time ASC LIMIT 1
               ) AS price_6m_later
        FROM fundamentals f
        WHERE f.fiscal_year IS NOT NULL AND f.eps IS NOT NULL
        ORDER BY f.ticker, f.fiscal_year
        """
    )

    df = pd.DataFrame(list(rows), columns=[
        "ticker", "fiscal_year", "eps", "nav", "pe",
        "cash_div_pct", "stock_div_pct", "sector_pe",
        "price_at_fy_end", "price_6m_later",
    ])
    for col in ["eps", "nav", "pe", "cash_div_pct", "stock_div_pct",
                "sector_pe", "price_at_fy_end", "price_6m_later"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    feature_rows = []
    for ticker, grp in df.groupby("ticker"):
        grp = grp.sort_values("fiscal_year").reset_index(drop=True)
        feats = compute_fundamental_features(grp)
        feats["pe_vs_sector"] = grp["pe"] / grp["sector_pe"].replace(0, np.nan)
        feats["ticker"] = ticker
        feats["fiscal_year"] = grp["fiscal_year"]
        feats["price_at_fy_end"] = grp["price_at_fy_end"].values
        feats["price_6m_later"] = grp["price_6m_later"].values
        feature_rows.append(feats)

    full_df = pd.concat(feature_rows, ignore_index=True)
    full_df = full_df.dropna(subset=["price_at_fy_end", "price_6m_later"])
    full_df["label"] = (full_df["price_6m_later"] > full_df["price_at_fy_end"]).astype(int)
    full_df = full_df.dropna(subset=FEATURE_COLS + ["label"])

    X = full_df[FEATURE_COLS].reset_index(drop=True)
    y = full_df["label"].reset_index(drop=True)
    return X, y


async def main() -> None:
    pool = await get_pool()

    log.info("Building training dataset...")
    X, y = await build_training_dataset(pool)
    log.info(f"Training set: {len(X)} rows, {y.mean():.1%} positive labels")

    if len(X) < 30:
        log.warning("Very few training samples — model may not generalize")

    # Walk-forward cross-validation
    tscv = TimeSeriesSplit(n_splits=min(3, len(X) // 20))
    auc_scores = []
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        scorer = FundamentalScorer()
        scorer.fit(X.iloc[train_idx], y.iloc[train_idx])
        if len(y.iloc[val_idx].unique()) < 2:
            log.warning(f"Fold {fold}: only one class in validation — skipping AUC")
            continue
        proba = scorer.predict_proba(X.iloc[val_idx])
        auc = roc_auc_score(y.iloc[val_idx], proba)
        auc_scores.append(auc)
        log.info(f"  Fold {fold} AUC: {auc:.3f}")

    if auc_scores:
        log.info(f"Mean CV AUC: {sum(auc_scores) / len(auc_scores):.3f}")

    log.info("Training final model on full dataset...")
    final = FundamentalScorer()
    final.fit(X, y)

    log.info("Feature importances:")
    for feat, imp in sorted(final.feature_importances().items(), key=lambda x: -x[1]):
        log.info(f"  {feat}: {imp:.3f}")

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    final.save(MODEL_PATH)
    log.info(f"Model saved → {MODEL_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Create models/v1/ directory placeholder**

```bash
mkdir -p models/v1
echo "# ML model artifacts — gitignored" > models/v1/.gitkeep
```

- [ ] **Step 3: Run training**

```bash
python -m ml.train.train_fundamental
```

Expected output (will vary by data):
```
INFO Building training dataset...
INFO Training set: N rows, ~50% positive labels
INFO   Fold 0 AUC: 0.xxx
INFO Mean CV AUC: 0.xxx
INFO Feature importances:
INFO   eps_growth_1yr: 0.xxx
...
INFO Model saved → models/v1/fundamental_scorer.pkl
```

> **Note:** With thin data (few hundred rows), AUC near 0.5 is expected. Model improves as more historical price data is loaded. The pipeline value is in the infrastructure — retrain quarterly.

- [ ] **Step 4: Verify model file exists**

```bash
ls models/v1/fundamental_scorer.pkl
```

- [ ] **Step 5: Commit training script**

```bash
git add ml/train/train_fundamental.py
git commit -m "feat(ml): XGBoost fundamental scorer training script"
```

---

## Task 9: Fundamental Scoring Inference

**Files:**
- Create: `ml/inference/score_fundamentals.py`
- Create: `tests/unit/ml/test_score_fundamentals.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/ml/test_score_fundamentals.py
import numpy as np
import pandas as pd
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


def _make_scorer_mock(proba: float = 0.7):
    """Mock FundamentalScorer that returns fixed probability."""
    scorer = MagicMock()
    scorer.predict_proba = MagicMock(return_value=np.array([proba]))
    return scorer


@pytest.mark.asyncio
async def test_score_returns_dict_per_ticker():
    from ml.inference.score_fundamentals import score_all_tickers
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[
        {"ticker": "GP"}, {"ticker": "BRACBANK"}
    ])

    feature_vec = pd.Series({
        "eps_growth_1yr": 0.1, "eps_growth_3yr": 0.05,
        "nav_growth": 0.06, "pe_vs_sector": 0.9,
        "div_yield": 0.03, "eps_consistency": 0.8,
    })

    scorer = _make_scorer_mock(0.65)

    with patch("ml.inference.score_fundamentals.build_fundamental_feature_vector",
               AsyncMock(return_value=feature_vec)):
        results = await score_all_tickers(pool, scorer)

    assert "GP" in results
    assert "BRACBANK" in results
    assert 0.0 <= results["GP"] <= 1.0


@pytest.mark.asyncio
async def test_score_skips_ticker_with_empty_features():
    from ml.inference.score_fundamentals import score_all_tickers
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[{"ticker": "NOBDATA"}])
    scorer = _make_scorer_mock()

    with patch("ml.inference.score_fundamentals.build_fundamental_feature_vector",
               AsyncMock(return_value=pd.Series(dtype=float))):
        results = await score_all_tickers(pool, scorer)

    assert "NOBDATA" not in results
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/unit/ml/test_score_fundamentals.py -v
```

- [ ] **Step 3: Implement score_fundamentals.py**

```python
# ml/inference/score_fundamentals.py
"""
Run FundamentalScorer on all active tickers and write scores to stock_scores table.

Run: python -m ml.inference.score_fundamentals
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from db.pool import get_pool
from ml.features.feature_store import build_fundamental_feature_vector
from ml.models.fundamental_scorer import FEATURE_COLS, FundamentalScorer

MODEL_PATH = Path("models/v1/fundamental_scorer.pkl")
MODEL_VERSION = "v1"
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def score_all_tickers(pool, scorer: FundamentalScorer) -> dict[str, float]:
    """
    Score all active tickers with FundamentalScorer.

    Returns dict mapping ticker → fundamental_score (0.0–1.0).
    Tickers with missing fundamental data are skipped.
    """
    tickers = await pool.fetch(
        "SELECT ticker FROM companies WHERE is_active = true ORDER BY ticker"
    )

    scores: dict[str, float] = {}
    for row in tickers:
        ticker = row["ticker"]
        try:
            feat_vec = await build_fundamental_feature_vector(pool, ticker)
            if feat_vec.empty or feat_vec.isna().all():
                log.debug(f"skip {ticker}: no fundamental data")
                continue
            feat_df = pd.DataFrame([feat_vec])
            proba = scorer.predict_proba(feat_df)
            scores[ticker] = float(proba[0])
        except Exception as exc:
            log.warning(f"skip {ticker}: {exc}")

    return scores


async def write_scores(pool, scores: dict[str, float], scored_at: datetime) -> int:
    """Upsert fundamental_score into stock_scores. Returns rows inserted."""
    count = 0
    for ticker, score in scores.items():
        await pool.execute(
            """
            INSERT INTO stock_scores
                (ticker, scored_at, fundamental_score, model_version)
            VALUES ($1, $2, $3, $4)
            """,
            ticker, scored_at, score, MODEL_VERSION,
        )
        count += 1
    return count


async def main() -> None:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Model not found at {MODEL_PATH}. Run ml.train.train_fundamental first."
        )

    pool = await get_pool()
    scorer = FundamentalScorer()
    scorer.load(MODEL_PATH)

    log.info("Scoring all active tickers...")
    scored_at = datetime.now(timezone.utc)
    scores = await score_all_tickers(pool, scorer)
    log.info(f"Scored {len(scores)} tickers")

    inserted = await write_scores(pool, scores, scored_at)
    log.info(f"Wrote {inserted} rows to stock_scores")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/unit/ml/test_score_fundamentals.py -v
```

Expected: `2 passed`

- [ ] **Step 5: Run inference against real DB**

```bash
python -m ml.inference.score_fundamentals
```

Expected:
```
INFO Scoring all active tickers...
INFO Scored NNN tickers
INFO Wrote NNN rows to stock_scores
```

- [ ] **Step 6: Verify in DB**

```bash
make db-shell
```
```sql
SELECT ticker, fundamental_score, scored_at FROM stock_scores ORDER BY fundamental_score DESC LIMIT 10;
\q
```

- [ ] **Step 7: Commit**

```bash
git add ml/inference/score_fundamentals.py tests/unit/ml/test_score_fundamentals.py
git commit -m "feat(ml): fundamental scoring inference — scores all tickers, writes to stock_scores"
```

---

## Task 10: LSTMPredictor Model Class

**Files:**
- Create: `ml/models/lstm_predictor.py`
- Create: `tests/unit/ml/test_lstm_predictor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/ml/test_lstm_predictor.py
import numpy as np
import pytest
import torch
from pathlib import Path
import tempfile


def _make_batch(batch=4, seq=60, features=10):
    return torch.randn(batch, seq, features)


def test_forward_output_shape():
    from ml.models.lstm_predictor import LSTMPredictor
    model = LSTMPredictor(input_size=10)
    x = _make_batch()
    out = model(x)
    assert out.shape == (4, 3), f"expected (4,3), got {out.shape}"


def test_forward_no_nan():
    from ml.models.lstm_predictor import LSTMPredictor
    model = LSTMPredictor(input_size=10)
    x = _make_batch()
    out = model(x)
    assert not torch.isnan(out).any()


def test_predict_proba_range():
    from ml.models.lstm_predictor import LSTMPredictor
    model = LSTMPredictor(input_size=10)
    x = _make_batch(batch=8)
    proba = model.predict_proba(x)
    assert proba.shape == (8, 3)
    assert (proba >= 0).all() and (proba <= 1).all()


def test_save_load_roundtrip():
    from ml.models.lstm_predictor import LSTMPredictor
    model = LSTMPredictor(input_size=10)
    x = _make_batch(batch=2)
    out_before = model.predict_proba(x)

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "lstm.pt"
        model.save(path)
        loaded = LSTMPredictor.load(path)
        out_after = loaded.predict_proba(x)

    np.testing.assert_array_almost_equal(
        out_before.numpy(), out_after.numpy(), decimal=5
    )
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/unit/ml/test_lstm_predictor.py -v
```

- [ ] **Step 3: Implement lstm_predictor.py**

```python
# ml/models/lstm_predictor.py
"""2-layer LSTM for DSE price direction prediction."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import torch
import torch.nn as nn

# 3 output heads: P(up) for 5d / 10d / 20d horizons
N_HORIZONS = 3


class LSTMPredictor(nn.Module):
    """
    2-layer LSTM predicting probability of price increase over 3 horizons.

    Input: (batch, seq_len, input_size) tensor of normalized price features.
    Output: (batch, 3) logits for [5d_up, 10d_up, 20d_up].
    """

    def __init__(
        self,
        input_size: int = 10,
        hidden_size: int = 64,
        num_layers: int = 2,
        dropout: float = 0.2,
    ) -> None:
        super().__init__()
        self._input_size = input_size
        self._hidden_size = hidden_size
        self._num_layers = num_layers

        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.head = nn.Linear(hidden_size, N_HORIZONS)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Returns logits (batch, 3). Apply sigmoid for probabilities."""
        _, (h_n, _) = self.lstm(x)
        last_hidden = h_n[-1]  # (batch, hidden_size)
        return self.head(last_hidden)

    def predict_proba(self, x: torch.Tensor) -> torch.Tensor:
        """Returns probabilities (batch, 3) in [0, 1]."""
        self.eval()
        with torch.no_grad():
            return torch.sigmoid(self(x))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "state_dict": self.state_dict(),
                "input_size": self._input_size,
                "hidden_size": self._hidden_size,
                "num_layers": self._num_layers,
            },
            path,
        )

    @classmethod
    def load(cls, path: Path) -> "LSTMPredictor":
        checkpoint: dict[str, Any] = torch.load(path, map_location="cpu")
        model = cls(
            input_size=checkpoint["input_size"],
            hidden_size=checkpoint["hidden_size"],
            num_layers=checkpoint["num_layers"],
        )
        model.load_state_dict(checkpoint["state_dict"])
        model.eval()
        return model
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/unit/ml/test_lstm_predictor.py -v
```

Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add ml/models/lstm_predictor.py tests/unit/ml/test_lstm_predictor.py
git commit -m "feat(ml): LSTMPredictor PyTorch model (2-layer, 3 horizon heads)"
```

---

## Task 11: Train LSTM

**Files:**
- Create: `ml/train/train_lstm.py`

- [ ] **Step 1: Create train_lstm.py**

```python
# ml/train/train_lstm.py
"""
Train LSTM price direction predictor.

Uses pooled data across all tickers (shared model, not per-ticker).
60-day input window → 5d/10d/20d binary direction label.

Run: python -m ml.train.train_lstm
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from db.pool import get_pool
from ml.features.price_features import compute_price_features
from ml.models.lstm_predictor import LSTMPredictor

MODEL_PATH = Path("models/v1/lstm_v0.pt")
SEQ_LEN = 60
HORIZONS = [5, 10, 20]
PRICE_FEATURE_COLS = [
    "rsi_14", "macd_diff", "bb_pband", "atr_norm", "adx_14",
    "return_1d", "return_5d", "return_20d", "volume_zscore", "obv",
]
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def build_sequences(pool) -> tuple[np.ndarray, np.ndarray]:
    """
    Fetch full-OHLCV price data from DB, compute features, build 60-day windows.

    Returns:
        X: (N, 60, n_features) float32
        y: (N, 3) float32 — binary labels for [5d_up, 10d_up, 20d_up]
    """
    rows = await pool.fetch(
        """
        SELECT ticker, time, close, high, low, volume
        FROM stock_prices
        WHERE quality_flag = 'ok' AND close IS NOT NULL
          AND high IS NOT NULL AND low IS NOT NULL
        ORDER BY ticker, time
        """
    )

    df = pd.DataFrame(list(rows), columns=["ticker", "time", "close", "high", "low", "volume"])
    for col in ["close", "high", "low", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    all_X, all_y = [], []
    max_horizon = max(HORIZONS)

    for ticker, grp in df.groupby("ticker"):
        grp = grp.sort_values("time").reset_index(drop=True)
        if len(grp) < SEQ_LEN + max_horizon:
            continue

        feats = compute_price_features(grp.set_index("time"))
        feats = feats[PRICE_FEATURE_COLS].ffill().fillna(0)
        close = grp["close"].values

        feat_arr = feats.values.astype(np.float32)
        n = len(feat_arr)

        for i in range(SEQ_LEN, n - max_horizon):
            window = feat_arr[i - SEQ_LEN:i]
            labels = np.array([
                int(close[i + h - 1] > close[i - 1])
                for h in HORIZONS
            ], dtype=np.float32)
            all_X.append(window)
            all_y.append(labels)

    if not all_X:
        raise ValueError("No training sequences built — check stock_prices data")

    return np.stack(all_X), np.stack(all_y)


async def main() -> None:
    pool = await get_pool()

    log.info("Building training sequences (may take 1–2 min)...")
    X, y = await build_sequences(pool)
    log.info(f"Sequences: {X.shape}, Labels: {y.shape}")
    log.info(f"Label distribution (5d/10d/20d up): {y.mean(axis=0)}")

    # Train/val split (80/20 chronological — do NOT shuffle to avoid leakage)
    split = int(len(X) * 0.8)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]

    train_ds = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train))
    val_ds   = TensorDataset(torch.from_numpy(X_val), torch.from_numpy(y_val))
    train_dl = DataLoader(train_ds, batch_size=64, shuffle=True)
    val_dl   = DataLoader(val_ds, batch_size=256, shuffle=False)

    model = LSTMPredictor(input_size=len(PRICE_FEATURE_COLS))
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    criterion = nn.BCEWithLogitsLoss()

    best_val_loss = float("inf")
    patience = 5
    patience_count = 0

    for epoch in range(50):
        model.train()
        train_losses = []
        for xb, yb in train_dl:
            optimizer.zero_grad()
            loss = criterion(model(xb), yb)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_losses.append(loss.item())

        model.eval()
        val_losses = []
        with torch.no_grad():
            for xb, yb in val_dl:
                val_losses.append(criterion(model(xb), yb).item())

        train_loss = sum(train_losses) / len(train_losses)
        val_loss   = sum(val_losses) / len(val_losses)
        log.info(f"Epoch {epoch+1:02d} | train={train_loss:.4f} val={val_loss:.4f}")

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            patience_count = 0
            model.save(MODEL_PATH)
            log.info(f"  → saved (val_loss={val_loss:.4f})")
        else:
            patience_count += 1
            if patience_count >= patience:
                log.info(f"Early stopping at epoch {epoch+1}")
                break

    log.info(f"Training complete. Best val_loss={best_val_loss:.4f}")
    log.info(f"Model saved → {MODEL_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run training**

```bash
python -m ml.train.train_lstm
```

Expected (thin data — may converge in 10–20 epochs):
```
INFO Building training sequences...
INFO Sequences: (N, 60, 10), Labels: (N, 3)
INFO Epoch 01 | train=0.xxxx val=0.xxxx
...
INFO Training complete. Best val_loss=0.xxxx
INFO Model saved → models/v1/lstm_v0.pt
```

> **Note:** With ~475 rows/ticker, val_loss near 0.69 (log(2) = random baseline) is expected. AUC improvement comes when more OHLCV history is loaded. Retrain quarterly.

- [ ] **Step 3: Commit**

```bash
git add ml/train/train_lstm.py
git commit -m "feat(ml): LSTM training script — pooled 60d windows, early stopping"
```

---

## Task 12: LSTM Inference

**Files:**
- Create: `ml/inference/predict_prices.py`
- Create: `tests/unit/ml/test_predict_prices.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/ml/test_predict_prices.py
import numpy as np
import pandas as pd
import pytest
import torch
from unittest.mock import AsyncMock, MagicMock, patch


def _make_feature_matrix(n: int = 70) -> pd.DataFrame:
    import datetime
    cols = [
        "rsi_14", "macd_diff", "bb_pband", "atr_norm", "adx_14",
        "return_1d", "return_5d", "return_20d", "volume_zscore", "obv",
        "close",
    ]
    rng = np.random.default_rng(0)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame(rng.standard_normal((n, len(cols))), columns=cols, index=idx)


@pytest.mark.asyncio
async def test_predict_writes_three_horizons():
    from ml.inference.predict_prices import predict_ticker
    pool = MagicMock()
    pool.execute = AsyncMock()

    feat_df = _make_feature_matrix(70)
    model = MagicMock()
    model.predict_proba = MagicMock(
        return_value=torch.tensor([[0.6, 0.55, 0.52]])
    )

    with patch("ml.inference.predict_prices.build_price_feature_matrix",
               AsyncMock(return_value=feat_df)):
        await predict_ticker(pool, model, "GP")

    # Should call pool.execute 3 times (one per horizon: 5d, 10d, 20d)
    assert pool.execute.call_count == 3


@pytest.mark.asyncio
async def test_predict_skips_ticker_no_data():
    from ml.inference.predict_prices import predict_ticker
    pool = MagicMock()
    pool.execute = AsyncMock()
    model = MagicMock()

    with patch("ml.inference.predict_prices.build_price_feature_matrix",
               AsyncMock(return_value=pd.DataFrame())):
        await predict_ticker(pool, model, "NOBDATA")

    pool.execute.assert_not_called()
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/unit/ml/test_predict_prices.py -v
```

- [ ] **Step 3: Implement predict_prices.py**

```python
# ml/inference/predict_prices.py
"""
Run LSTM inference on all active tickers and write to ml_predictions.

Run: python -m ml.inference.predict_prices
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from db.pool import get_pool
from ml.features.feature_store import build_price_feature_matrix
from ml.models.lstm_predictor import LSTMPredictor

MODEL_PATH = Path("models/v1/lstm_v0.pt")
MODEL_VERSION = "lstm_v0"
SEQ_LEN = 60
HORIZONS = [5, 10, 20]
PRICE_FEATURE_COLS = [
    "rsi_14", "macd_diff", "bb_pband", "atr_norm", "adx_14",
    "return_1d", "return_5d", "return_20d", "volume_zscore", "obv",
]
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def predict_ticker(pool, model: LSTMPredictor, ticker: str) -> None:
    """Run LSTM for one ticker and write 3 rows (one per horizon) to ml_predictions."""
    feat_df = await build_price_feature_matrix(pool, ticker, lookback_days=SEQ_LEN + 5)

    if feat_df.empty or len(feat_df) < SEQ_LEN:
        log.debug(f"skip {ticker}: insufficient price data ({len(feat_df)} rows)")
        return

    available_cols = [c for c in PRICE_FEATURE_COLS if c in feat_df.columns]
    window = feat_df[available_cols].tail(SEQ_LEN).values.astype(np.float32)

    # Pad features to match model input_size if columns differ
    if window.shape[1] < len(PRICE_FEATURE_COLS):
        pad = np.zeros((SEQ_LEN, len(PRICE_FEATURE_COLS) - window.shape[1]), dtype=np.float32)
        window = np.hstack([window, pad])

    x = torch.from_numpy(window).unsqueeze(0)  # (1, 60, n_features)
    proba = model.predict_proba(x).squeeze(0)  # (3,)

    predicted_at = datetime.now(timezone.utc)
    last_close = float(feat_df["close"].iloc[-1]) if "close" in feat_df.columns else None

    for i, horizon in enumerate(HORIZONS):
        p = float(proba[i])
        direction = "up" if p >= 0.5 else "down"
        confidence = p if direction == "up" else 1.0 - p
        target_price = (last_close * (1 + (p - 0.5) * 0.1)) if last_close else None

        await pool.execute(
            """
            INSERT INTO ml_predictions
                (ticker, predicted_at, horizon_days, direction, confidence,
                 target_price, model_version)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            ticker, predicted_at, horizon, direction, confidence,
            target_price, MODEL_VERSION,
        )


async def main() -> None:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"LSTM model not found at {MODEL_PATH}. Run ml.train.train_lstm first."
        )

    pool = await get_pool()
    model = LSTMPredictor.load(MODEL_PATH)
    model.eval()

    tickers = await pool.fetch(
        "SELECT ticker FROM companies WHERE is_active = true ORDER BY ticker"
    )
    log.info(f"Running LSTM inference on {len(tickers)} tickers...")

    ok, skipped = 0, 0
    for row in tickers:
        ticker = row["ticker"]
        try:
            await predict_ticker(pool, model, ticker)
            ok += 1
        except Exception as exc:
            log.warning(f"skip {ticker}: {exc}")
            skipped += 1

    log.info(f"Done. ok={ok} skipped={skipped}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/unit/ml/test_predict_prices.py -v
```

Expected: `2 passed`

- [ ] **Step 5: Run inference**

```bash
python -m ml.inference.predict_prices
```

- [ ] **Step 6: Verify in DB**

```bash
make db-shell
```
```sql
SELECT ticker, horizon_days, direction, confidence, predicted_at
FROM ml_predictions ORDER BY predicted_at DESC LIMIT 15;
\q
```

- [ ] **Step 7: Commit**

```bash
git add ml/inference/predict_prices.py tests/unit/ml/test_predict_prices.py
git commit -m "feat(ml): LSTM price direction inference — writes ml_predictions for all tickers"
```

---

## Task 13: DCF Calculator

**Files:**
- Create: `ml/valuation/dcf.py`
- Create: `tests/unit/ml/test_dcf.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/ml/test_dcf.py
import pytest
from ml.valuation.dcf import DCFCalculator


def test_intrinsic_value_positive_eps():
    calc = DCFCalculator(
        eps_ttm=10.0,
        eps_growth_rate=0.10,
        cost_of_equity=0.12,
        terminal_growth=0.03,
        projection_years=5,
    )
    result = calc.calculate(current_price=100.0)
    assert result["intrinsic_value"] > 0
    assert "margin_of_safety_pct" in result


def test_margin_of_safety_negative_when_overvalued():
    calc = DCFCalculator(
        eps_ttm=5.0,
        eps_growth_rate=0.05,
        cost_of_equity=0.12,
        terminal_growth=0.03,
        projection_years=5,
    )
    result = calc.calculate(current_price=500.0)
    assert result["margin_of_safety_pct"] < 0


def test_margin_of_safety_positive_when_undervalued():
    calc = DCFCalculator(
        eps_ttm=15.0,
        eps_growth_rate=0.15,
        cost_of_equity=0.12,
        terminal_growth=0.03,
        projection_years=5,
    )
    result = calc.calculate(current_price=50.0)
    assert result["margin_of_safety_pct"] > 0


def test_zero_or_negative_eps_returns_none():
    calc = DCFCalculator(
        eps_ttm=0.0,
        eps_growth_rate=0.10,
        cost_of_equity=0.12,
        terminal_growth=0.03,
        projection_years=5,
    )
    result = calc.calculate(current_price=100.0)
    assert result["intrinsic_value"] is None


def test_result_keys():
    calc = DCFCalculator(10.0, 0.10, 0.12, 0.03, 5)
    result = calc.calculate(100.0)
    assert set(result.keys()) >= {"intrinsic_value", "margin_of_safety_pct",
                                   "pv_earnings", "terminal_value"}
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/unit/ml/test_dcf.py -v
```

- [ ] **Step 3: Implement dcf.py**

```python
# ml/valuation/dcf.py
"""Discounted Cash Flow intrinsic value calculator."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class DCFCalculator:
    """
    Simple DDM-style DCF using EPS as proxy for free cash flow per share.

    Assumes: FCF ≈ EPS (appropriate for asset-light businesses; overestimates
    for capital-intensive sectors like banking/cement — use with awareness).
    """
    eps_ttm: float
    eps_growth_rate: float      # expected annual growth for projection_years
    cost_of_equity: float       # WACC proxy (risk-free + beta × market premium)
    terminal_growth: float      # long-run growth after projection period
    projection_years: int = 5

    def calculate(self, current_price: float) -> dict:
        """
        Returns dict with keys: intrinsic_value, margin_of_safety_pct,
        pv_earnings, terminal_value. Values are None if EPS <= 0.
        """
        if self.eps_ttm <= 0:
            return {
                "intrinsic_value": None,
                "margin_of_safety_pct": None,
                "pv_earnings": None,
                "terminal_value": None,
            }

        r = self.cost_of_equity
        g = self.eps_growth_rate
        g_t = self.terminal_growth

        # PV of projected earnings
        pv_earnings = 0.0
        eps = self.eps_ttm
        for year in range(1, self.projection_years + 1):
            eps = eps * (1 + g)
            pv_earnings += eps / (1 + r) ** year

        # Terminal value (Gordon Growth Model)
        eps_terminal = self.eps_ttm * (1 + g) ** self.projection_years
        if r <= g_t:
            terminal_value = 0.0  # model breaks down — cost < terminal growth
        else:
            terminal_value = (eps_terminal * (1 + g_t)) / (r - g_t)
        pv_terminal = terminal_value / (1 + r) ** self.projection_years

        intrinsic_value = pv_earnings + pv_terminal
        margin_of_safety_pct = (intrinsic_value - current_price) / current_price * 100

        return {
            "intrinsic_value": round(intrinsic_value, 2),
            "margin_of_safety_pct": round(margin_of_safety_pct, 2),
            "pv_earnings": round(pv_earnings, 2),
            "terminal_value": round(pv_terminal, 2),
        }
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/unit/ml/test_dcf.py -v
```

Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add ml/valuation/dcf.py tests/unit/ml/test_dcf.py
git commit -m "feat(ml): DCF calculator (EPS-based, Gordon Growth terminal value)"
```

---

## Task 14: Monte Carlo Simulator

**Files:**
- Create: `ml/valuation/monte_carlo.py`
- Create: `tests/unit/ml/test_monte_carlo.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/ml/test_monte_carlo.py
import numpy as np
import pytest
from ml.valuation.monte_carlo import MonteCarloSimulator


def test_output_keys():
    sim = MonteCarloSimulator(
        eps_ttm=10.0, eps_growth_mean=0.10, eps_growth_std=0.05,
        cost_of_equity=0.12, terminal_growth=0.03, n_scenarios=100,
    )
    result = sim.simulate(current_price=100.0)
    assert set(result.keys()) >= {"p10", "p50", "p90", "mean", "downside_prob"}


def test_p10_lt_p50_lt_p90():
    sim = MonteCarloSimulator(
        eps_ttm=10.0, eps_growth_mean=0.10, eps_growth_std=0.05,
        cost_of_equity=0.12, terminal_growth=0.03, n_scenarios=1000,
    )
    result = sim.simulate(current_price=100.0)
    assert result["p10"] < result["p50"] < result["p90"]


def test_downside_prob_range():
    sim = MonteCarloSimulator(
        eps_ttm=10.0, eps_growth_mean=0.10, eps_growth_std=0.05,
        cost_of_equity=0.12, terminal_growth=0.03, n_scenarios=500,
    )
    result = sim.simulate(current_price=100.0)
    assert 0.0 <= result["downside_prob"] <= 1.0


def test_reproducible_with_seed():
    kwargs = dict(eps_ttm=10.0, eps_growth_mean=0.10, eps_growth_std=0.05,
                  cost_of_equity=0.12, terminal_growth=0.03, n_scenarios=200, seed=42)
    r1 = MonteCarloSimulator(**kwargs).simulate(100.0)
    r2 = MonteCarloSimulator(**kwargs).simulate(100.0)
    assert r1["p50"] == r2["p50"]
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/unit/ml/test_monte_carlo.py -v
```

- [ ] **Step 3: Implement monte_carlo.py**

```python
# ml/valuation/monte_carlo.py
"""Monte Carlo DCF simulation over EPS growth uncertainty."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ml.valuation.dcf import DCFCalculator


@dataclass
class MonteCarloSimulator:
    """
    Run DCFCalculator across N scenarios where EPS growth is sampled from
    a normal distribution. Outputs P10/P50/P90 intrinsic value range.
    """
    eps_ttm: float
    eps_growth_mean: float
    eps_growth_std: float
    cost_of_equity: float
    terminal_growth: float = 0.03
    projection_years: int = 5
    n_scenarios: int = 10_000
    seed: Optional[int] = None

    def simulate(self, current_price: float) -> dict:
        """
        Returns dict with keys: p10, p50, p90, mean, downside_prob.
        downside_prob = fraction of scenarios where intrinsic_value < current_price.
        """
        rng = np.random.default_rng(self.seed)
        growth_samples = rng.normal(self.eps_growth_mean, self.eps_growth_std, self.n_scenarios)
        # Clip to avoid absurd values (e.g. -200% growth)
        growth_samples = np.clip(growth_samples, -0.5, 1.0)

        values = []
        for g in growth_samples:
            calc = DCFCalculator(
                eps_ttm=self.eps_ttm,
                eps_growth_rate=float(g),
                cost_of_equity=self.cost_of_equity,
                terminal_growth=self.terminal_growth,
                projection_years=self.projection_years,
            )
            result = calc.calculate(current_price)
            iv = result["intrinsic_value"]
            if iv is not None and iv > 0:
                values.append(iv)

        if not values:
            return {"p10": None, "p50": None, "p90": None, "mean": None, "downside_prob": None}

        arr = np.array(values)
        return {
            "p10": round(float(np.percentile(arr, 10)), 2),
            "p50": round(float(np.percentile(arr, 50)), 2),
            "p90": round(float(np.percentile(arr, 90)), 2),
            "mean": round(float(arr.mean()), 2),
            "downside_prob": round(float((arr < current_price).mean()), 4),
        }
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/unit/ml/test_monte_carlo.py -v
```

Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add ml/valuation/monte_carlo.py tests/unit/ml/test_monte_carlo.py
git commit -m "feat(ml): Monte Carlo DCF simulator (P10/P50/P90 intrinsic value range)"
```

---

## Task 15: Health Score + Nightly Inference Job

**Files:**
- Create: `ml/scoring/health_score.py`
- Create: `tests/unit/ml/test_health_score.py`
- Modify: `extraction/scheduler.py`

- [ ] **Step 1: Write failing tests for health_score**

```python
# tests/unit/ml/test_health_score.py
import pytest
from ml.scoring.health_score import compute_health_score


def test_all_scores_present():
    score = compute_health_score(
        fundamental_score=0.7,
        momentum_score=0.6,
        valuation_score=0.8,
        sentiment_score=0.5,
    )
    assert 0 <= score <= 100


def test_missing_scores_use_neutral():
    # With only fundamental_score, others default to 0.5 (neutral)
    score = compute_health_score(fundamental_score=1.0)
    assert 0 <= score <= 100


def test_all_zero_gives_low_score():
    score = compute_health_score(
        fundamental_score=0.0,
        momentum_score=0.0,
        valuation_score=0.0,
        sentiment_score=0.0,
    )
    assert score < 10


def test_all_one_gives_high_score():
    score = compute_health_score(
        fundamental_score=1.0,
        momentum_score=1.0,
        valuation_score=1.0,
        sentiment_score=1.0,
    )
    assert score > 90


def test_score_clipped_0_to_100():
    score = compute_health_score(
        fundamental_score=1.5,  # out of range — should clip
        momentum_score=1.5,
        valuation_score=1.5,
        sentiment_score=1.5,
    )
    assert score <= 100
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/unit/ml/test_health_score.py -v
```

- [ ] **Step 3: Implement health_score.py**

```python
# ml/scoring/health_score.py
"""Composite Stock Health Score (0–100)."""
from __future__ import annotations

from typing import Optional


WEIGHTS = {
    "fundamental": 0.35,
    "momentum":    0.35,
    "valuation":   0.20,
    "sentiment":   0.10,
}
NEUTRAL = 0.5  # default when a sub-score is missing


def compute_health_score(
    fundamental_score: Optional[float] = None,
    momentum_score: Optional[float] = None,
    valuation_score: Optional[float] = None,
    sentiment_score: Optional[float] = None,
) -> float:
    """
    Compute composite health score 0–100.

    Missing sub-scores are treated as NEUTRAL (0.5) so they neither help nor
    hurt. All sub-scores should be in [0, 1] — values outside are clipped.
    """
    scores = {
        "fundamental": min(max(fundamental_score or NEUTRAL, 0.0), 1.0),
        "momentum":    min(max(momentum_score    or NEUTRAL, 0.0), 1.0),
        "valuation":   min(max(valuation_score   or NEUTRAL, 0.0), 1.0),
        "sentiment":   min(max(sentiment_score   or NEUTRAL, 0.0), 1.0),
    }
    composite = sum(scores[k] * w for k, w in WEIGHTS.items())
    return round(min(max(composite * 100, 0.0), 100.0), 2)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/unit/ml/test_health_score.py -v
```

Expected: `5 passed`

- [ ] **Step 5: Add nightly ML job to scheduler**

In `extraction/scheduler.py`, add `job_nightly_ml()` after the existing job functions:

```python
async def job_nightly_ml() -> None:
    """
    Nightly ML inference pipeline (runs after EOD snapshot, ~22:00 BD time).

    Steps:
    1. Fundamental scoring (XGBoost) → stock_scores.fundamental_score
    2. Price direction (LSTM) → ml_predictions
    3. DCF valuation → stock_scores.valuation_score
    4. Composite health score → stock_scores.health_score
    """
    from pathlib import Path
    from db.pool import get_pool
    from extraction.jobs import job_run

    async with job_run("nightly_ml") as ctx:
        pool = await get_pool()

        # ── 1. Fundamental scoring ──────────────────────────────────────
        fund_path = Path("models/v1/fundamental_scorer.pkl")
        if fund_path.exists():
            from ml.models.fundamental_scorer import FundamentalScorer
            from ml.inference.score_fundamentals import score_all_tickers, write_scores
            from datetime import datetime, timezone

            scorer = FundamentalScorer()
            scorer.load(fund_path)
            scored_at = datetime.now(timezone.utc)
            fund_scores = await score_all_tickers(pool, scorer)
            await write_scores(pool, fund_scores, scored_at)
            logger.info("nightly_ml: fundamental scoring done", n=len(fund_scores))
            ctx["records_inserted"] = len(fund_scores)
        else:
            logger.warning("nightly_ml: fundamental_scorer.pkl not found — skipping")
            fund_scores = {}

        # ── 2. LSTM price direction ────────────────────────────────────
        lstm_path = Path("models/v1/lstm_v0.pt")
        if lstm_path.exists():
            import torch
            from ml.models.lstm_predictor import LSTMPredictor
            from ml.inference.predict_prices import predict_ticker

            model = LSTMPredictor.load(lstm_path)
            model.eval()
            tickers = await pool.fetch(
                "SELECT ticker FROM companies WHERE is_active = true"
            )
            ok = 0
            for row in tickers:
                try:
                    await predict_ticker(pool, model, row["ticker"])
                    ok += 1
                except Exception as exc:
                    logger.warning("nightly_ml: lstm skip", ticker=row["ticker"], error=str(exc))
            logger.info("nightly_ml: LSTM inference done", n=ok)
        else:
            logger.warning("nightly_ml: lstm_v0.pt not found — skipping")

        # ── 3. DCF valuation score ─────────────────────────────────────
        from datetime import datetime, timezone
        from ml.valuation.dcf import DCFCalculator

        tickers = await pool.fetch(
            "SELECT ticker FROM companies WHERE is_active = true"
        )
        now = datetime.now(timezone.utc)
        for row in tickers:
            ticker = row["ticker"]
            try:
                fund_row = await pool.fetchrow(
                    """
                    SELECT eps, pe, cash_div_pct FROM fundamentals
                    WHERE ticker = $1 AND fiscal_year IS NOT NULL
                    ORDER BY fiscal_year DESC LIMIT 1
                    """,
                    ticker,
                )
                price_row = await pool.fetchrow(
                    "SELECT close FROM stock_prices WHERE ticker = $1 ORDER BY time DESC LIMIT 1",
                    ticker,
                )
                if not fund_row or not price_row or not fund_row["eps"]:
                    continue

                eps_rows = await pool.fetch(
                    """
                    SELECT eps FROM fundamentals
                    WHERE ticker = $1 AND fiscal_year IS NOT NULL AND eps IS NOT NULL
                    ORDER BY fiscal_year DESC LIMIT 3
                    """,
                    ticker,
                )
                eps_vals = [float(r["eps"]) for r in eps_rows if r["eps"]]
                if len(eps_vals) < 2:
                    continue
                growth = (eps_vals[0] / eps_vals[-1]) ** (1 / len(eps_vals)) - 1
                growth = max(min(growth, 0.30), -0.20)

                calc = DCFCalculator(
                    eps_ttm=float(fund_row["eps"]),
                    eps_growth_rate=growth,
                    cost_of_equity=0.12,
                    terminal_growth=0.03,
                )
                dcf = calc.calculate(float(price_row["close"]))

                if dcf["margin_of_safety_pct"] is not None:
                    mos = dcf["margin_of_safety_pct"]
                    valuation_score = min(max((mos + 50) / 100, 0.0), 1.0)
                    fund_score = fund_scores.get(ticker)
                    health = compute_health_score_from_db(fund_score, valuation_score)
                    await pool.execute(
                        """
                        UPDATE stock_scores SET valuation_score = $1, health_score = $2
                        WHERE ticker = $3 AND scored_at = (
                            SELECT MAX(scored_at) FROM stock_scores WHERE ticker = $3
                        )
                        """,
                        valuation_score, health, ticker,
                    )
            except Exception as exc:
                logger.warning("nightly_ml: dcf skip", ticker=ticker, error=str(exc))

        logger.info("nightly_ml: complete")


def compute_health_score_from_db(
    fundamental_score: float | None,
    valuation_score: float | None,
) -> float:
    from ml.scoring.health_score import compute_health_score
    return compute_health_score(
        fundamental_score=fundamental_score,
        valuation_score=valuation_score,
    )
```

Also add to the `get_scheduler()` wiring section — at the bottom of `scheduler.py` add the job registration:

```python
# In the function that registers jobs (or inline in get_scheduler):
scheduler.add_job(
    job_nightly_ml,
    trigger="cron",
    hour=22, minute=0,
    timezone=BD_TZ,
    id="nightly_ml",
    replace_existing=True,
    misfire_grace_time=3600,
)
```

> **Location:** Add this `scheduler.add_job(...)` block in `_configure_production_mode()` in `extraction/scheduler.py` (line ~374), after the `job_health_checks` registration block. Also add `(job_nightly_ml, "nightly_ml", cfg.test_nightly_ml_minutes)` to the `job_map` list in `_configure_test_mode()` (line ~406). You'll also need to add a `test_nightly_ml_minutes: int = 15` field to `mgmt/config.py` Settings alongside the other test timing fields.

- [ ] **Step 6: Run health_score tests**

```bash
pytest tests/unit/ml/test_health_score.py -v
```

Expected: `5 passed`

- [ ] **Step 7: Run full ML unit test suite**

```bash
pytest tests/unit/ml/ -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add ml/scoring/health_score.py tests/unit/ml/test_health_score.py extraction/scheduler.py
git commit -m "feat(ml): health score composite + nightly ML scheduler job"
```

---

## Task 16: Accuracy Monitoring

**Files:**
- Create: `ml/monitoring/accuracy_report.py`
- Create: `tests/unit/ml/test_accuracy_report.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/ml/test_accuracy_report.py
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_populate_outcomes_marks_correct():
    from ml.monitoring.accuracy_report import populate_outcomes

    pool = MagicMock()
    # Simulate: prediction was "up", actual price went up
    pool.fetch = AsyncMock(return_value=[
        {
            "id": 1, "ticker": "GP", "horizon_days": 5,
            "predicted_at": None, "predicted_direction": "up",
            "confidence": 0.65, "price_at_prediction": 100.0, "price_at_horizon": 110.0,
        }
    ])
    pool.execute = AsyncMock()

    await populate_outcomes(pool)

    pool.execute.assert_called_once()
    call_args = pool.execute.call_args[0]
    # 5th positional arg is `correct` (True — price went up, prediction was "up")
    assert call_args[5] is True


@pytest.mark.asyncio
async def test_generate_report_returns_dict():
    from ml.monitoring.accuracy_report import generate_report

    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[
        {"horizon_days": 5,  "total": 100, "correct": 55, "accuracy": 0.55},
        {"horizon_days": 10, "total": 100, "correct": 52, "accuracy": 0.52},
        {"horizon_days": 20, "total": 100, "correct": 51, "accuracy": 0.51},
    ])

    report = await generate_report(pool)
    assert "by_horizon" in report
    assert len(report["by_horizon"]) == 3
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/unit/ml/test_accuracy_report.py -v
```

- [ ] **Step 3: Implement accuracy_report.py**

```python
# ml/monitoring/accuracy_report.py
"""
Populate prediction_outcomes once horizons pass and generate accuracy report.

Run populate_outcomes() nightly (after job_nightly_ml).
Run generate_report() weekly or on demand.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger(__name__)


async def populate_outcomes(pool) -> int:
    """
    Find ml_predictions whose horizon has passed but outcome is unknown,
    look up actual price, and write to prediction_outcomes.

    Returns count of outcomes evaluated.
    """
    now = datetime.now(timezone.utc)

    # Fetch predictions whose horizon has passed and outcome not yet recorded
    pending = await pool.fetch(
        """
        SELECT p.id, p.ticker, p.horizon_days, p.predicted_at,
               p.predicted_direction, p.confidence,
               (
                   SELECT sp.close FROM stock_prices sp
                   WHERE sp.ticker = p.ticker
                     AND sp.time >= p.predicted_at - INTERVAL '1 day'
                     AND sp.time <= p.predicted_at + INTERVAL '2 days'
                   ORDER BY sp.time ASC LIMIT 1
               ) AS price_at_prediction,
               (
                   SELECT sp2.close FROM stock_prices sp2
                   WHERE sp2.ticker = p.ticker
                     AND sp2.time >= p.predicted_at + (p.horizon_days || ' days')::interval
                     AND sp2.time <= p.predicted_at + ((p.horizon_days + 3) || ' days')::interval
                   ORDER BY sp2.time ASC LIMIT 1
               ) AS price_at_horizon
        FROM ml_predictions p
        WHERE NOT EXISTS (
            SELECT 1 FROM prediction_outcomes po WHERE po.prediction_id = p.id
        )
          AND p.predicted_at + (p.horizon_days || ' days')::interval < $1
        """,
        now,
    )

    count = 0
    for row in pending:
        p_price = row["price_at_prediction"]
        h_price = row["price_at_horizon"]

        if p_price is None or h_price is None:
            continue

        p_price = float(p_price)
        h_price = float(h_price)
        return_pct = (h_price - p_price) / p_price * 100
        actual_direction = "up" if h_price > p_price else "down"
        correct = actual_direction == row["predicted_direction"]

        await pool.execute(
            """
            INSERT INTO prediction_outcomes
                (prediction_id, ticker, horizon_days, predicted_at,
                 predicted_direction, confidence,
                 price_at_prediction, price_at_horizon, return_pct,
                 actual_direction, correct, evaluated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT DO NOTHING
            """,
            row["id"], row["ticker"], row["horizon_days"], row["predicted_at"],
            row["predicted_direction"], float(row["confidence"]),
            p_price, h_price, return_pct, actual_direction, correct,
            datetime.now(timezone.utc),
        )
        count += 1

    log.info(f"populate_outcomes: evaluated {count} predictions")
    return count


async def generate_report(pool) -> dict:
    """
    Generate accuracy report by horizon.

    Returns dict with 'by_horizon' key containing list of {horizon_days, total, correct, accuracy}.
    """
    rows = await pool.fetch(
        """
        SELECT horizon_days,
               COUNT(*) AS total,
               SUM(CASE WHEN correct THEN 1 ELSE 0 END) AS correct,
               AVG(CASE WHEN correct THEN 1.0 ELSE 0.0 END) AS accuracy
        FROM prediction_outcomes
        WHERE correct IS NOT NULL
        GROUP BY horizon_days
        ORDER BY horizon_days
        """
    )

    by_horizon = [
        {
            "horizon_days": int(r["horizon_days"]),
            "total": int(r["total"]),
            "correct": int(r["correct"]),
            "accuracy": float(r["accuracy"]),
        }
        for r in rows
    ]

    return {"by_horizon": by_horizon, "generated_at": datetime.now(timezone.utc).isoformat()}


async def main() -> None:
    from db.pool import get_pool
    pool = await get_pool()

    count = await populate_outcomes(pool)
    print(f"Evaluated {count} new prediction outcomes")

    report = await generate_report(pool)
    print("\nAccuracy by horizon:")
    for h in report["by_horizon"]:
        print(f"  {h['horizon_days']:2d}d: {h['accuracy']:.1%} ({h['correct']}/{h['total']})")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/unit/ml/test_accuracy_report.py -v
```

Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add ml/monitoring/accuracy_report.py tests/unit/ml/test_accuracy_report.py
git commit -m "feat(ml): accuracy monitoring — populate outcomes + report by horizon"
```

---

## Task 17b: Accuracy-Gated Quarterly Retrain

**Why:** After predictions age past their horizon, we know which were right. Quarterly retrain
pulls that new labeled data into training, closing the feedback loop. If accuracy drops below
48% directional (barely above random), we also fire an alert — and still retrain.

**Files:**
- Modify: `ml/monitoring/accuracy_report.py` — add `check_accuracy_thresholds()`
- Modify: `extraction/scheduler.py` — replace `job_quarterly()` TODO stub with real retrain
- Modify: `tests/unit/ml/test_accuracy_report.py` — add threshold tests

- [ ] **Step 1: Write failing threshold tests**

Add to `tests/unit/ml/test_accuracy_report.py`:

```python
@pytest.mark.asyncio
async def test_check_accuracy_returns_critical_below_45pct():
    from ml.monitoring.accuracy_report import check_accuracy_thresholds
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[
        {"horizon_days": 5, "total": 100, "correct": 43, "accuracy": 0.43},
    ])
    result = await check_accuracy_thresholds(pool, warning_threshold=0.48, critical_threshold=0.45)
    assert result["alert_level"] == "CRITICAL"
    assert result["worst_horizon"] == 5


@pytest.mark.asyncio
async def test_check_accuracy_returns_warning_between_45_and_48():
    from ml.monitoring.accuracy_report import check_accuracy_thresholds
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[
        {"horizon_days": 10, "total": 100, "correct": 47, "accuracy": 0.47},
    ])
    result = await check_accuracy_thresholds(pool, warning_threshold=0.48, critical_threshold=0.45)
    assert result["alert_level"] == "WARNING"


@pytest.mark.asyncio
async def test_check_accuracy_returns_none_when_ok():
    from ml.monitoring.accuracy_report import check_accuracy_thresholds
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[
        {"horizon_days": 5, "total": 100, "correct": 55, "accuracy": 0.55},
        {"horizon_days": 20, "total": 100, "correct": 52, "accuracy": 0.52},
    ])
    result = await check_accuracy_thresholds(pool)
    assert result["alert_level"] is None


@pytest.mark.asyncio
async def test_check_accuracy_skips_small_sample_horizons():
    from ml.monitoring.accuracy_report import check_accuracy_thresholds
    pool = MagicMock()
    # Only 5 outcomes — below min_samples=30, should not trigger alert
    pool.fetch = AsyncMock(return_value=[
        {"horizon_days": 5, "total": 5, "correct": 0, "accuracy": 0.0},
    ])
    result = await check_accuracy_thresholds(pool, min_samples=30)
    assert result["alert_level"] is None
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/unit/ml/test_accuracy_report.py -k "threshold" -v
```

- [ ] **Step 3: Add `check_accuracy_thresholds()` to accuracy_report.py**

```python
async def check_accuracy_thresholds(
    pool,
    warning_threshold: float = 0.48,
    critical_threshold: float = 0.45,
    min_samples: int = 30,
) -> dict:
    """
    Check if any horizon's directional accuracy has dropped below thresholds.

    Skips horizons with fewer than min_samples evaluated outcomes — not
    enough data to distinguish model failure from statistical noise.

    Returns dict with keys:
        alert_level: "CRITICAL" | "WARNING" | None
        worst_horizon: int | None  (horizon with lowest accuracy)
        worst_accuracy: float | None
        report: list[dict]  (full by-horizon breakdown)
    """
    report = await generate_report(pool)
    by_horizon = report["by_horizon"]

    worst_accuracy = 1.0
    worst_horizon = None

    for h in by_horizon:
        if h["total"] < min_samples:
            continue
        if h["accuracy"] < worst_accuracy:
            worst_accuracy = h["accuracy"]
            worst_horizon = h["horizon_days"]

    if worst_horizon is None:
        return {
            "alert_level": None,
            "worst_horizon": None,
            "worst_accuracy": None,
            "report": by_horizon,
        }

    if worst_accuracy < critical_threshold:
        alert_level: str | None = "CRITICAL"
    elif worst_accuracy < warning_threshold:
        alert_level = "WARNING"
    else:
        alert_level = None

    return {
        "alert_level": alert_level,
        "worst_horizon": worst_horizon,
        "worst_accuracy": worst_accuracy,
        "report": by_horizon,
    }
```

- [ ] **Step 4: Run threshold tests — expect PASS**

```bash
pytest tests/unit/ml/test_accuracy_report.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Replace `job_quarterly()` stub in `extraction/scheduler.py`**

Replace the current stub (lines ~274–279):

```python
async def job_quarterly() -> None:
    """Quarterly ML retrain (Jan/Apr/Jul/Oct 1st, 03:00 BD time).

    1. Catch up any unevaluated prediction outcomes.
    2. Check accuracy thresholds — fire alert if degraded.
    3. Retrain XGBoost + LSTM on expanded dataset (includes periods model previously predicted).
    4. Save versioned models (models/YYYYMMDD/) + overwrite current (models/v1/).
    5. Fire INFO alert with retrain summary.
    """
    import shutil
    from datetime import datetime, timezone
    from pathlib import Path

    from db.pool import get_pool
    from extraction.jobs import job_run
    from extraction.observability import fire_alert
    from ml.monitoring.accuracy_report import check_accuracy_thresholds, populate_outcomes

    async with job_run("quarterly_retrain") as ctx:
        pool = await get_pool()

        # ── 1. Catch up outcomes ───────────────────────────────────────
        n_outcomes = await populate_outcomes(pool)
        logger.info("quarterly_retrain: outcomes populated", extra={"n": n_outcomes})

        # ── 2. Accuracy check ──────────────────────────────────────────
        check = await check_accuracy_thresholds(pool)
        if check["alert_level"]:
            logger.warning(
                "quarterly_retrain: accuracy degraded",
                extra={
                    "level": check["alert_level"],
                    "horizon": check["worst_horizon"],
                    "accuracy": check["worst_accuracy"],
                },
            )
            await fire_alert(
                severity=check["alert_level"],
                stream_name="ml_predictions",
                message=(
                    f"ML accuracy degraded: {check['worst_horizon']}d horizon = "
                    f"{check['worst_accuracy']:.1%} directional accuracy"
                ),
                details=check,
            )

        # ── 3 + 4. Retrain ─────────────────────────────────────────────
        version = datetime.now(timezone.utc).strftime("%Y%m%d")
        versioned_dir = Path(f"models/{version}")
        current_dir = Path("models/v1")
        versioned_dir.mkdir(parents=True, exist_ok=True)
        current_dir.mkdir(parents=True, exist_ok=True)

        errors: list[str] = []

        # XGBoost retrain
        try:
            from ml.models.fundamental_scorer import FundamentalScorer
            from ml.train.train_fundamental import build_training_dataset

            X, y = await build_training_dataset(pool)
            if len(X) >= 30:
                scorer = FundamentalScorer()
                scorer.fit(X, y)
                scorer.save(versioned_dir / "fundamental_scorer.pkl")
                shutil.copy(versioned_dir / "fundamental_scorer.pkl", current_dir / "fundamental_scorer.pkl")
                logger.info("quarterly_retrain: XGBoost retrained", extra={"samples": len(X)})
            else:
                logger.warning("quarterly_retrain: XGBoost skipped — insufficient data", extra={"n": len(X)})
        except Exception as exc:
            logger.error("quarterly_retrain: XGBoost failed", extra={"error": str(exc)})
            errors.append(f"XGBoost: {exc}")

        # LSTM retrain
        try:
            import torch
            import torch.nn as nn
            from torch.utils.data import DataLoader, TensorDataset

            from ml.models.lstm_predictor import LSTMPredictor
            from ml.train.train_lstm import PRICE_FEATURE_COLS, build_sequences

            X_arr, y_arr = await build_sequences(pool)
            if len(X_arr) >= 200:
                split = int(len(X_arr) * 0.8)
                train_dl = DataLoader(
                    TensorDataset(torch.from_numpy(X_arr[:split]), torch.from_numpy(y_arr[:split])),
                    batch_size=64, shuffle=True,
                )
                val_dl = DataLoader(
                    TensorDataset(torch.from_numpy(X_arr[split:]), torch.from_numpy(y_arr[split:])),
                    batch_size=256, shuffle=False,
                )
                model = LSTMPredictor(input_size=len(PRICE_FEATURE_COLS))
                optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
                criterion = nn.BCEWithLogitsLoss()
                best_val_loss = float("inf")
                patience_count = 0
                lstm_path = versioned_dir / "lstm_v0.pt"

                for _ in range(30):
                    model.train()
                    for xb, yb in train_dl:
                        optimizer.zero_grad()
                        loss = criterion(model(xb), yb)
                        loss.backward()
                        nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                        optimizer.step()
                    model.eval()
                    val_loss = sum(
                        criterion(model(xb), yb).item() for xb, yb in val_dl
                    ) / len(val_dl)
                    if val_loss < best_val_loss:
                        best_val_loss = val_loss
                        patience_count = 0
                        model.save(lstm_path)
                    else:
                        patience_count += 1
                        if patience_count >= 5:
                            break

                shutil.copy(lstm_path, current_dir / "lstm_v0.pt")
                logger.info("quarterly_retrain: LSTM retrained", extra={"best_val_loss": best_val_loss})
            else:
                logger.warning("quarterly_retrain: LSTM skipped — insufficient sequences", extra={"n": len(X_arr)})
        except Exception as exc:
            logger.error("quarterly_retrain: LSTM failed", extra={"error": str(exc)})
            errors.append(f"LSTM: {exc}")

        # ── 5. Summary alert ───────────────────────────────────────────
        await fire_alert(
            severity="INFO",
            stream_name="ml_predictions",
            message=f"Quarterly ML retrain complete (version={version})",
            details={"version": version, "errors": errors, "accuracy_check": check},
        )

        ctx["records_inserted"] = n_outcomes
        logger.info("quarterly_retrain: complete", extra={"version": version, "errors": errors})
```

- [ ] **Step 6: Run all accuracy_report tests**

```bash
pytest tests/unit/ml/test_accuracy_report.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add ml/monitoring/accuracy_report.py tests/unit/ml/test_accuracy_report.py extraction/scheduler.py
git commit -m "feat(ml): accuracy-gated quarterly retrain — threshold checks + versioned model saves"
```

---

## Task 17: Full Test Suite + Smoke Verification

- [ ] **Step 1: Run all ML unit tests**

```bash
pytest tests/unit/ml/ -v
```

Expected: all tests pass (should be ~35+ tests total).

- [ ] **Step 2: Run full unit test suite — verify no regressions**

```bash
make test
```

Expected: all existing tests still pass.

- [ ] **Step 3: Smoke run end-to-end ML pipeline**

```bash
python -m ml.train.train_fundamental
python -m ml.inference.score_fundamentals
python -m ml.train.train_lstm
python -m ml.inference.predict_prices
python -m ml.monitoring.accuracy_report
```

- [ ] **Step 4: Verify DB has predictions and scores**

```bash
make db-shell
```
```sql
SELECT COUNT(*) FROM ml_predictions;
SELECT COUNT(*) FROM stock_scores;
SELECT ticker, health_score, fundamental_score, scored_at
FROM stock_scores ORDER BY health_score DESC NULLS LAST LIMIT 10;
\q
```

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat(ml): Layer 3 ML prediction engine complete — XGBoost + LSTM + DCF + health score"
```

---

## Execution Order Notes

**Can run immediately (data ready):**
- Tasks 1–9: deps, DB, features, XGBoost train + inference

**Requires `quality_flag = 'ok'` OHLCV rows (BDShare data):**
- Tasks 10–12: LSTM train + inference (uses 2024–2026 OHLCV)

**No data dependency (pure math):**
- Tasks 13–14: DCF + Monte Carlo

**Requires tasks 7 + 12 scores to exist in DB:**
- Task 15: health score composite

**Requires ml_predictions to age past horizon:**
- Task 16: accuracy monitoring (meaningful results ~1 month after predictions start)
