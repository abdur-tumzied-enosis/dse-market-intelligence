# LSTM Ranking Model Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the near-random binary-direction LSTM with a cross-sectional return-ranking model that predicts forward rank-return over 6 Fibonacci horizons and selects a top-N basket, evaluated by rank IC and a backtest.

**Architecture:** Per-date cross-sectional normalization of features and labels turns the problem into "which stocks outperform". An attention-pooled LSTM with a swappable front-end slot regresses rank-return for horizons `[1,2,3,5,8,13]`. Training logs rank IC + top-N hit rate; a standalone backtest is the acceptance gate.

**Tech Stack:** Python, PyTorch, pandas, numpy, scikit-learn, `ta`, asyncpg, pytest.

**Reference spec:** `docs/superpowers/specs/2026-06-05-lstm-ranking-model-redesign-design.md`

---

## File Structure

- **Create** `ml/constants.py` — single source for `SEQ_LEN`, `HORIZONS`, `PRICE_FEATURE_COLS`, `EMA_SPAN`, `TOP_N`, `MIN_TICKERS_PER_DATE`. Kills the constant duplication between `train_lstm.py` and `predict_prices.py`.
- **Create** `ml/features/cross_sectional.py` — pure functions: `forward_returns`, `cross_sectional_zscore`, `build_windows`. Shared by train, dump, inference.
- **Create** `ml/eval/__init__.py`, `ml/eval/metrics.py` — `rank_ic`, `top_n_hit_rate`.
- **Create** `ml/eval/backtest.py` — top-N basket backtest + equity/Sharpe/drawdown.
- **Modify** `ml/features/price_features.py` — add `gap_open`, `body`, `mid_return` (uses `open`).
- **Modify** `ml/models/lstm_predictor.py` — attention pooling, `front_end` slot, 6 regression heads, drop `predict_proba` → `predict`.
- **Modify** `ml/train/train_lstm.py` — panelized `build_sequences` (open + EMA + cross-sectional norm + rank labels + meta), scheduler, IC/hit logging, updated `--dump`.
- **Modify** `ml/inference/predict_prices.py` — batch cross-sectional inference, regression output, ranking.
- **Modify tests** `tests/unit/ml/test_price_features.py`, `tests/unit/ml/test_lstm_predictor.py`.
- **Create tests** `tests/unit/ml/test_cross_sectional.py`, `tests/unit/ml/test_eval_metrics.py`, `tests/unit/ml/test_backtest.py`.

---

## Task 1: Shared constants module

**Files:**
- Create: `ml/constants.py`
- Test: `tests/unit/ml/test_constants.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/ml/test_constants.py
"""Tests for ml.constants."""
from __future__ import annotations


def test_horizons_are_fibonacci():
    from ml.constants import HORIZONS
    assert HORIZONS == [1, 2, 3, 5, 8, 13]


def test_feature_cols_count():
    from ml.constants import PRICE_FEATURE_COLS
    # 10 legacy + 3 new open-based
    assert len(PRICE_FEATURE_COLS) == 13
    assert PRICE_FEATURE_COLS[:10] == [
        "rsi_14", "macd_diff", "bb_pband", "atr_norm", "adx_14",
        "return_1d", "return_5d", "return_20d", "volume_zscore", "obv",
    ]
    assert PRICE_FEATURE_COLS[10:] == ["gap_open", "body", "mid_return"]


def test_scalar_constants():
    from ml.constants import SEQ_LEN, EMA_SPAN, TOP_N, MIN_TICKERS_PER_DATE
    assert SEQ_LEN == 60
    assert EMA_SPAN == 3
    assert TOP_N == 20
    assert MIN_TICKERS_PER_DATE == 20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/ml/test_constants.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ml.constants'`

- [ ] **Step 3: Write minimal implementation**

```python
# ml/constants.py
"""Shared ML constants — single source of truth for train + inference."""
from __future__ import annotations

SEQ_LEN = 60
HORIZONS = [1, 2, 3, 5, 8, 13]
EMA_SPAN = 3
TOP_N = 20
MIN_TICKERS_PER_DATE = 20

PRICE_FEATURE_COLS = [
    "rsi_14", "macd_diff", "bb_pband", "atr_norm", "adx_14",
    "return_1d", "return_5d", "return_20d", "volume_zscore", "obv",
    "gap_open", "body", "mid_return",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/ml/test_constants.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add ml/constants.py tests/unit/ml/test_constants.py
git commit -m "feat(ml): shared constants module for horizons/features"
```

---

## Task 2: Open-based price features

**Files:**
- Modify: `ml/features/price_features.py`
- Test: `tests/unit/ml/test_price_features.py`

- [ ] **Step 1: Update the test helper and add failing tests**

Replace the `_make_ohlcv` helper and `REQUIRED_COLS` in `tests/unit/ml/test_price_features.py` to include `open`, and add the new-column tests:

```python
REQUIRED_COLS = [
    "rsi_14", "macd_diff", "bb_pband", "atr_norm", "adx_14",
    "return_1d", "return_5d", "return_20d", "volume_zscore", "obv",
    "gap_open", "body", "mid_return",
]


def _make_ohlcv(n: int = 70) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    price = 100 + rng.standard_normal(n).cumsum()
    price = np.abs(price) + 10  # ensure positive
    close = price
    open_ = close + rng.uniform(-1, 1, n)
    return pd.DataFrame(
        {
            "open": open_,
            "close": close,
            "high": np.maximum(close, open_) + rng.uniform(0.5, 2, n),
            "low": np.minimum(close, open_) - rng.uniform(0.5, 2, n),
            "volume": rng.integers(100_000, 1_000_000, n).astype(float),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="D"),
    )


def test_open_based_features_present():
    from ml.features.price_features import compute_price_features
    result = compute_price_features(_make_ohlcv())
    for col in ["gap_open", "body", "mid_return"]:
        assert col in result.columns, f"missing column: {col}"


def test_body_matches_formula():
    from ml.features.price_features import compute_price_features
    df = _make_ohlcv()
    result = compute_price_features(df)
    expected = (df["close"] - df["open"]) / df["open"]
    pd.testing.assert_series_equal(
        result["body"], expected, check_names=False
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/ml/test_price_features.py -v`
Expected: FAIL — `test_open_based_features_present` (KeyError/missing col) and `test_body_matches_formula`.

- [ ] **Step 3: Implement the new features**

In `ml/features/price_features.py`, update the docstring column list to include `open`, read `open`, and append the 3 columns before `return result`:

```python
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)
    open_ = df["open"].astype(float)
```

Then immediately before `return result` add:

```python
    prev_close = close.shift(1)
    result["gap_open"] = open_ / prev_close - 1.0
    result["body"] = (close - open_) / open_.replace(0, np.nan)
    mid = (high + low) / 2.0
    result["mid_return"] = mid.pct_change(1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/ml/test_price_features.py -v`
Expected: PASS (all, including `test_no_infinite_values` which now covers the new cols).

- [ ] **Step 5: Commit**

```bash
git add ml/features/price_features.py tests/unit/ml/test_price_features.py
git commit -m "feat(ml): add gap_open/body/mid_return price features"
```

---

## Task 3: Cross-sectional utilities

**Files:**
- Create: `ml/features/cross_sectional.py`
- Test: `tests/unit/ml/test_cross_sectional.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/ml/test_cross_sectional.py
"""Tests for ml.features.cross_sectional."""
from __future__ import annotations

import numpy as np
import pandas as pd


def test_forward_returns_formula():
    from ml.features.cross_sectional import forward_returns
    close = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0])
    out = forward_returns(close, [1, 2])
    # fwd_ret_1[t] = close[t+1]/close[t] - 1
    assert out["fwd_ret_1"].iloc[0] == 11.0 / 10.0 - 1.0
    assert out["fwd_ret_2"].iloc[0] == 12.0 / 10.0 - 1.0
    # tail rows have no future -> NaN
    assert np.isnan(out["fwd_ret_1"].iloc[-1])


def test_cross_sectional_zscore_zero_mean():
    from ml.features.cross_sectional import cross_sectional_zscore
    df = pd.DataFrame({
        "time": ["d1", "d1", "d1", "d2", "d2", "d2"],
        "f": [1.0, 2.0, 3.0, 10.0, 20.0, 30.0],
    })
    out = cross_sectional_zscore(df, ["f"], by="time")
    # each date's z-scored values sum to ~0
    for _, grp in out.groupby("time"):
        assert abs(grp["f"].mean()) < 1e-9


def test_cross_sectional_zscore_constant_group_is_zero():
    from ml.features.cross_sectional import cross_sectional_zscore
    df = pd.DataFrame({"time": ["d1", "d1"], "f": [5.0, 5.0]})
    out = cross_sectional_zscore(df, ["f"], by="time")
    # zero std -> filled with 0, not NaN/inf
    assert (out["f"] == 0.0).all()


def test_build_windows_shapes_and_alignment():
    from ml.features.cross_sectional import build_windows
    # 2 tickers, 8 days each, 2 features, 1 label
    rows = []
    for tk in ["A", "B"]:
        for d in range(8):
            rows.append({
                "time": d, "ticker": tk,
                "f1": float(d), "f2": float(d * 2),
                "y_1": float(d), "raw_1": float(d) / 100,
            })
    panel = pd.DataFrame(rows)
    X, y, meta = build_windows(
        panel, feature_cols=["f1", "f2"],
        label_cols=["y_1"], raw_cols=["raw_1"], seq_len=3,
    )
    # rows per ticker with full window AND non-nan label: t in [2..7] = 6
    assert X.shape == (12, 3, 2)
    assert y.shape == (12, 1)
    assert len(meta) == 12
    assert set(meta["ticker"]) == {"A", "B"}
    assert "raw_1" in meta.columns
    # first A window covers days 0,1,2 in feature 1
    a0 = X[0, :, 0]
    np.testing.assert_array_equal(a0, np.array([0.0, 1.0, 2.0], dtype=np.float32))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/ml/test_cross_sectional.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ml.features.cross_sectional'`

- [ ] **Step 3: Implement the module**

```python
# ml/features/cross_sectional.py
"""Cross-sectional helpers: forward returns, per-date z-scoring, windowing."""
from __future__ import annotations

import numpy as np
import pandas as pd


def forward_returns(close: pd.Series, horizons: list[int]) -> pd.DataFrame:
    """Forward return per horizon: fwd_ret_h[t] = close[t+h]/close[t] - 1.

    Tail rows where close[t+h] is unavailable are NaN.
    """
    out = pd.DataFrame(index=close.index)
    close = close.astype(float)
    for h in horizons:
        out[f"fwd_ret_{h}"] = close.shift(-h) / close - 1.0
    return out


def cross_sectional_zscore(
    df: pd.DataFrame, cols: list[str], by: str = "time"
) -> pd.DataFrame:
    """Z-score `cols` within each `by` group (per-date cross-section).

    Zero-variance groups (or single-member) become 0.0, never NaN/inf.
    """
    out = df.copy()
    grp = df.groupby(by)[cols]
    mean = grp.transform("mean")
    std = grp.transform("std").replace(0.0, np.nan)
    z = (df[cols] - mean) / std
    out[cols] = z.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return out


def build_windows(
    panel: pd.DataFrame,
    feature_cols: list[str],
    label_cols: list[str],
    raw_cols: list[str],
    seq_len: int,
) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    """Slice per-ticker sequences from a long [time, ticker, ...] panel.

    Args:
        panel: long DataFrame, already cross-sectionally normalized, with a
            `time` and `ticker` column plus feature/label/raw columns.
        feature_cols: model inputs.
        label_cols: training targets (cross-sectional z of forward returns).
        raw_cols: raw forward returns, carried into meta for IC/backtest.
        seq_len: window length.

    Returns:
        X: (N, seq_len, n_features) float32
        y: (N, n_labels) float32
        meta: DataFrame (N rows) with [time, ticker, *raw_cols] aligned to X/y.
        Samples whose label row contains NaN (no future return) are dropped.
    """
    Xs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    meta_rows: list[dict] = []

    for ticker, grp in panel.groupby("ticker", sort=False):
        grp = grp.sort_values("time").reset_index(drop=True)
        feat = grp[feature_cols].to_numpy(dtype=np.float32)
        lab = grp[label_cols].to_numpy(dtype=np.float32)
        raw = grp[raw_cols].to_numpy(dtype=np.float32)
        times = grp["time"].to_numpy()
        n = len(grp)
        for t in range(seq_len - 1, n):
            label = lab[t]
            if np.isnan(label).any():
                continue
            Xs.append(feat[t - seq_len + 1: t + 1])
            ys.append(label)
            row = {"time": times[t], "ticker": ticker}
            for j, rc in enumerate(raw_cols):
                row[rc] = float(raw[t, j])
            meta_rows.append(row)

    if not Xs:
        raise ValueError("No windows built — check panel size vs seq_len")

    return np.stack(Xs), np.stack(ys), pd.DataFrame(meta_rows)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/ml/test_cross_sectional.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add ml/features/cross_sectional.py tests/unit/ml/test_cross_sectional.py
git commit -m "feat(ml): cross-sectional forward returns, z-score, windowing"
```

---

## Task 4: Evaluation metrics

**Files:**
- Create: `ml/eval/__init__.py`, `ml/eval/metrics.py`
- Test: `tests/unit/ml/test_eval_metrics.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/ml/test_eval_metrics.py
"""Tests for ml.eval.metrics."""
from __future__ import annotations

import numpy as np


def test_rank_ic_perfect_positive():
    from ml.eval.metrics import rank_ic
    pred = np.array([1.0, 2.0, 3.0, 4.0])
    actual = np.array([10.0, 20.0, 30.0, 40.0])
    assert abs(rank_ic(pred, actual) - 1.0) < 1e-9


def test_rank_ic_perfect_negative():
    from ml.eval.metrics import rank_ic
    pred = np.array([1.0, 2.0, 3.0, 4.0])
    actual = np.array([40.0, 30.0, 20.0, 10.0])
    assert abs(rank_ic(pred, actual) + 1.0) < 1e-9


def test_rank_ic_too_few_is_nan():
    from ml.eval.metrics import rank_ic
    assert np.isnan(rank_ic(np.array([1.0]), np.array([2.0])))


def test_top_n_hit_rate():
    from ml.eval.metrics import top_n_hit_rate
    # top-2 by pred = indices 3,2 (actual 0.9, 0.1); median actual = 0.15
    pred = np.array([0.0, 1.0, 2.0, 3.0])
    actual = np.array([-0.5, 0.2, 0.1, 0.9])
    # picks actual[3]=0.9 (>med) and actual[2]=0.1 (<med) -> 0.5
    assert top_n_hit_rate(pred, actual, n=2) == 0.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/ml/test_eval_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ml.eval'`

- [ ] **Step 3: Implement the module**

```python
# ml/eval/__init__.py
```

```python
# ml/eval/metrics.py
"""Signal-quality metrics for cross-sectional return ranking."""
from __future__ import annotations

import numpy as np
import pandas as pd


def rank_ic(pred: np.ndarray, actual: np.ndarray) -> float:
    """Spearman rank correlation between predictions and realized returns.

    Computed as Pearson correlation of the ranks. Returns NaN if fewer than
    2 valid (non-NaN) pairs or if either side has zero rank variance.
    """
    pred = np.asarray(pred, dtype=float)
    actual = np.asarray(actual, dtype=float)
    mask = ~(np.isnan(pred) | np.isnan(actual))
    if mask.sum() < 2:
        return float("nan")
    pr = pd.Series(pred[mask]).rank().to_numpy()
    ar = pd.Series(actual[mask]).rank().to_numpy()
    if pr.std() == 0 or ar.std() == 0:
        return float("nan")
    return float(np.corrcoef(pr, ar)[0, 1])


def top_n_hit_rate(pred: np.ndarray, actual: np.ndarray, n: int) -> float:
    """Fraction of the top-`n` predicted names whose realized return beats the
    cross-sectional median realized return."""
    pred = np.asarray(pred, dtype=float)
    actual = np.asarray(actual, dtype=float)
    if len(pred) == 0:
        return float("nan")
    k = min(n, len(pred))
    top_idx = np.argsort(pred)[::-1][:k]
    median = np.median(actual)
    return float((actual[top_idx] > median).mean())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/ml/test_eval_metrics.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add ml/eval/__init__.py ml/eval/metrics.py tests/unit/ml/test_eval_metrics.py
git commit -m "feat(ml): rank IC and top-N hit-rate metrics"
```

---

## Task 5: Attention-pooled regression LSTM

**Files:**
- Modify: `ml/models/lstm_predictor.py`
- Test: `tests/unit/ml/test_lstm_predictor.py`

- [ ] **Step 1: Rewrite the tests for the new model contract**

Replace the entire body of `tests/unit/ml/test_lstm_predictor.py`:

```python
"""Tests for ml.models.lstm_predictor."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import torch


def _make_batch(batch=4, seq=60, features=13):
    return torch.randn(batch, seq, features)


def test_forward_output_shape():
    from ml.models.lstm_predictor import LSTMPredictor
    model = LSTMPredictor(input_size=13)
    out = model(_make_batch())
    assert out.shape == (4, 6), f"expected (4,6), got {out.shape}"


def test_forward_no_nan():
    from ml.models.lstm_predictor import LSTMPredictor
    model = LSTMPredictor(input_size=13)
    assert not torch.isnan(model(_make_batch())).any()


def test_predict_returns_raw_regression():
    from ml.models.lstm_predictor import LSTMPredictor
    model = LSTMPredictor(input_size=13)
    out = model.predict(_make_batch(batch=8))
    assert out.shape == (8, 6)
    # regression output is unbounded — not constrained to [0,1]
    assert out.dtype == torch.float32


def test_front_end_defaults_to_identity():
    import torch.nn as nn
    from ml.models.lstm_predictor import LSTMPredictor
    model = LSTMPredictor(input_size=13)
    assert isinstance(model.front_end, nn.Identity)


def test_save_load_roundtrip():
    from ml.models.lstm_predictor import LSTMPredictor
    model = LSTMPredictor(input_size=13)
    x = _make_batch(batch=2)
    before = model.predict(x)
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "lstm.pt"
        model.save(path)
        loaded = LSTMPredictor.load(path)
        after = loaded.predict(x)
    np.testing.assert_array_almost_equal(before.numpy(), after.numpy(), decimal=5)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/ml/test_lstm_predictor.py -v`
Expected: FAIL — shape `(4,3)` vs `(4,6)`, no `predict`, no `front_end`.

- [ ] **Step 3: Rewrite the model**

Replace the entire contents of `ml/models/lstm_predictor.py`:

```python
"""Attention-pooled LSTM for DSE cross-sectional return ranking."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import torch
import torch.nn as nn

# Regression heads: predicted rank-return for [1,2,3,5,8,13]d horizons
N_HORIZONS = 6


class AttentionPool(nn.Module):
    """Learned-query attention pooling over the time dimension."""

    def __init__(self, hidden_size: int) -> None:
        super().__init__()
        self.score = nn.Linear(hidden_size, 1)

    def forward(self, seq: torch.Tensor) -> torch.Tensor:
        # seq: (batch, time, hidden) -> (batch, hidden)
        weights = torch.softmax(self.score(seq).squeeze(-1), dim=1)  # (B, T)
        return torch.bmm(weights.unsqueeze(1), seq).squeeze(1)


class LSTMPredictor(nn.Module):
    """Front-end → LSTM → attention pool → linear heads.

    Input:  (batch, seq_len, input_size) normalized features.
    Output: (batch, N_HORIZONS) regression of cross-sectional rank-return.

    `front_end` is a swappable slot (default nn.Identity) so a Conv1d block can
    be added later without changing the LSTM/attention/heads.
    """

    def __init__(
        self,
        input_size: int = 13,
        hidden_size: int = 64,
        num_layers: int = 2,
        dropout: float = 0.4,
    ) -> None:
        super().__init__()
        self._input_size = input_size
        self._hidden_size = hidden_size
        self._num_layers = num_layers

        self.front_end: nn.Module = nn.Identity()
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.pool = AttentionPool(hidden_size)
        self.head = nn.Linear(hidden_size, N_HORIZONS)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.front_end(x)
        seq_out, _ = self.lstm(x)          # (batch, seq_len, hidden)
        pooled = self.pool(seq_out)        # (batch, hidden)
        return self.head(pooled)           # (batch, N_HORIZONS)

    def predict(self, x: torch.Tensor) -> torch.Tensor:
        """Returns raw regression outputs (batch, N_HORIZONS)."""
        self.eval()
        with torch.no_grad():
            return self(x)

    def save(self, path: Path, scaler: Any = None) -> None:
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
        checkpoint: dict[str, Any] = torch.load(
            path, map_location="cpu", weights_only=False
        )
        model = cls(
            input_size=checkpoint["input_size"],
            hidden_size=checkpoint["hidden_size"],
            num_layers=checkpoint["num_layers"],
        )
        model.load_state_dict(checkpoint["state_dict"])
        model.eval()
        return model
```

Note: `load` now returns the model only (the global scaler is gone — normalization is cross-sectional at inference). Callers that did `model, _ = load(...)` must change (Task 8). `save` keeps the `scaler` kwarg for call-site compatibility but ignores it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/ml/test_lstm_predictor.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add ml/models/lstm_predictor.py tests/unit/ml/test_lstm_predictor.py
git commit -m "feat(ml): attention-pooled regression LSTM with front-end slot"
```

---

## Task 6: Rewrite build_sequences (panel + cross-sectional + meta)

**Files:**
- Modify: `ml/train/train_lstm.py`
- Test: `tests/unit/ml/test_build_sequences.py`

This task replaces the data-prep half of `train_lstm.py`. `build_sequences` becomes synchronous and pure over a fetched DataFrame, so it is unit-testable without a DB. A thin async `load_price_rows(pool)` does the fetch.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/ml/test_build_sequences.py
"""Tests for train_lstm.build_sequences (DB-free, pure DataFrame in)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _fake_price_df(n_tickers=25, n_days=120):
    rng = np.random.default_rng(7)
    frames = []
    for k in range(n_tickers):
        base = 50 + k
        close = base + rng.standard_normal(n_days).cumsum()
        close = np.abs(close) + 5
        open_ = close + rng.uniform(-1, 1, n_days)
        frames.append(pd.DataFrame({
            "ticker": f"T{k:03d}",
            "time": pd.date_range("2023-01-01", periods=n_days, freq="D"),
            "open": open_,
            "close": close,
            "high": np.maximum(close, open_) + rng.uniform(0.2, 1.5, n_days),
            "low": np.minimum(close, open_) - rng.uniform(0.2, 1.5, n_days),
            "volume": rng.integers(1e5, 1e6, n_days).astype(float),
        }))
    return pd.concat(frames, ignore_index=True)


def test_build_sequences_shapes():
    from ml.train.train_lstm import build_sequences
    from ml.constants import SEQ_LEN, HORIZONS, PRICE_FEATURE_COLS
    X, y, meta = build_sequences(_fake_price_df())
    assert X.ndim == 3
    assert X.shape[1] == SEQ_LEN
    assert X.shape[2] == len(PRICE_FEATURE_COLS)
    assert y.shape[1] == len(HORIZONS)
    assert len(meta) == X.shape[0]
    for h in HORIZONS:
        assert f"fwd_ret_{h}" in meta.columns
    assert {"time", "ticker"}.issubset(meta.columns)


def test_build_sequences_no_nan_in_features():
    from ml.train.train_lstm import build_sequences
    X, y, meta = build_sequences(_fake_price_df())
    assert not np.isnan(X).any()
    assert not np.isnan(y).any()


def test_build_sequences_labels_are_cross_sectional_zscored():
    from ml.train.train_lstm import build_sequences
    X, y, meta = build_sequences(_fake_price_df())
    # group y by date via meta; each date-cross-section mean ~ 0
    df = meta.copy()
    df["y0"] = y[:, 0]
    means = df.groupby("time")["y0"].mean()
    # most dates have >1 ticker and should center near 0
    assert means.abs().mean() < 0.2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/ml/test_build_sequences.py -v`
Expected: FAIL — `build_sequences` currently async, returns 2-tuple, no `open`/meta.

- [ ] **Step 3: Replace the top of `train_lstm.py` and `build_sequences`**

Replace everything from the imports down to the end of `build_sequences` (original lines 1–97) with:

```python
"""
Train cross-sectional rank-return LSTM (shared model across all tickers).

60-day input window → cross-sectionally z-scored forward-return labels for
horizons [1,2,3,5,8,13]. Selection: rank predicted rank-return, buy top-N.

Run: python -m ml.train.train_lstm
     python -m ml.train.train_lstm --dump dataset.npz
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from db.pool import get_pool
from ml.constants import (
    EMA_SPAN,
    HORIZONS,
    MIN_TICKERS_PER_DATE,
    PRICE_FEATURE_COLS,
    SEQ_LEN,
    TOP_N,
)
from ml.eval.metrics import rank_ic, top_n_hit_rate
from ml.features.cross_sectional import (
    build_windows,
    cross_sectional_zscore,
    forward_returns,
)
from ml.features.price_features import compute_price_features
from ml.models.lstm_predictor import LSTMPredictor

MODEL_PATH = Path("models/v1/lstm_v0.pt")
LABEL_COLS = [f"y_{h}" for h in HORIZONS]
RAW_COLS = [f"fwd_ret_{h}" for h in HORIZONS]

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def load_price_rows(pool) -> pd.DataFrame:
    """Fetch full OHLCV for all quality-ok tickers as a long DataFrame."""
    rows = await pool.fetch(
        """
        SELECT ticker, time, open, close, high, low, volume
        FROM stock_prices
        WHERE quality_flag = 'ok' AND close IS NOT NULL
          AND open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL
        ORDER BY ticker, time
        """
    )
    df = pd.DataFrame(
        list(rows),
        columns=["ticker", "time", "open", "close", "high", "low", "volume"],
    )
    for col in ["open", "close", "high", "low", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def build_sequences(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    """Long OHLCV DataFrame → (X, y, meta).

    Pipeline: per-ticker features + EMA smoothing + forward returns → long panel
    → per-date cross-sectional z-score of features and forward returns → drop
    thin dates → 60-day windows.

    Returns:
        X: (N, SEQ_LEN, n_features) float32
        y: (N, n_horizons) float32 — cross-sectional z of forward returns
        meta: DataFrame (N rows): time, ticker, raw fwd_ret_h per horizon
    """
    panels: list[pd.DataFrame] = []
    max_h = max(HORIZONS)

    for ticker, grp in df.groupby("ticker"):
        grp = grp.sort_values("time").reset_index(drop=True)
        if len(grp) < SEQ_LEN + max_h:
            continue

        feats = compute_price_features(grp.set_index("time"))
        feats = (
            feats[PRICE_FEATURE_COLS]
            .replace([np.inf, -np.inf], np.nan)
            .ffill()
            .fillna(0.0)
        )
        # EMA smoothing (causal, per ticker) to cut daily noise
        feats = feats.ewm(span=EMA_SPAN, adjust=False).mean()

        fwd = forward_returns(grp.set_index("time")["close"], HORIZONS)

        panel = feats.copy()
        for h in HORIZONS:
            panel[f"fwd_ret_{h}"] = fwd[f"fwd_ret_{h}"].to_numpy()
        panel["ticker"] = ticker
        panel = panel.reset_index().rename(columns={"index": "time"})
        if "time" not in panel.columns:  # index name was already "time"
            panel = panel.rename(columns={panel.columns[0]: "time"})
        panels.append(panel)

    if not panels:
        raise ValueError("No tickers with enough history — check stock_prices")

    long_df = pd.concat(panels, ignore_index=True)

    # Drop thin cross-sections (too few tickers to z-score meaningfully)
    counts = long_df.groupby("time")["ticker"].transform("count")
    long_df = long_df[counts >= MIN_TICKERS_PER_DATE].reset_index(drop=True)

    # Cross-sectional z-score of features (model inputs)
    long_df = cross_sectional_zscore(long_df, PRICE_FEATURE_COLS, by="time")

    # Cross-sectional z-score of forward returns -> training labels y_h.
    # Keep raw fwd_ret_h alongside for IC/backtest. Rows with NaN fwd_ret
    # (tail of each ticker) get y=NaN and are dropped by build_windows.
    z = cross_sectional_zscore(long_df, RAW_COLS, by="time")
    for h in HORIZONS:
        long_df[f"y_{h}"] = z[f"fwd_ret_{h}"]
        # restore NaN where the raw forward return was missing (no future)
        long_df.loc[long_df[f"fwd_ret_{h}"].isna(), f"y_{h}"] = np.nan

    X, y, meta = build_windows(
        long_df,
        feature_cols=PRICE_FEATURE_COLS,
        label_cols=LABEL_COLS,
        raw_cols=RAW_COLS,
        seq_len=SEQ_LEN,
    )
    nan_count = int(np.isnan(X).sum())
    if nan_count:
        raise ValueError(f"NaN in feature array after cleaning: {nan_count}")
    return X, y, meta
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/ml/test_build_sequences.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add ml/train/train_lstm.py tests/unit/ml/test_build_sequences.py
git commit -m "feat(ml): panelized cross-sectional build_sequences with rank labels"
```

---

## Task 7: Training loop — scheduler, IC logging, dump

**Files:**
- Modify: `ml/train/train_lstm.py` (the `main`, `dump`, and `__main__` sections — original lines 100–199)

No new unit test (the loop is I/O + DB bound; `build_sequences` and metrics are already covered). Verification is a manual smoke run noted at the end.

- [ ] **Step 1: Replace `main`, `dump`, and `__main__`**

Replace everything from `async def main()` to the end of the file with:

```python
def _epoch_metrics(model, X_val, meta_val) -> dict[str, float]:
    """Compute per-horizon mean rank IC and top-N hit rate over val dates."""
    model.eval()
    with torch.no_grad():
        preds = model(torch.from_numpy(X_val)).numpy()  # (N, n_horizons)
    out: dict[str, float] = {}
    df = meta_val.reset_index(drop=True)
    for hi, h in enumerate(HORIZONS):
        ics, hits = [], []
        actual_col = f"fwd_ret_{h}"
        df_h = df.assign(_pred=preds[:, hi])
        for _, g in df_h.groupby("time"):
            a = g[actual_col].to_numpy()
            p = g["_pred"].to_numpy()
            ic = rank_ic(p, a)
            if not np.isnan(ic):
                ics.append(ic)
            hits.append(top_n_hit_rate(p, a, n=TOP_N))
        out[f"ic_{h}"] = float(np.mean(ics)) if ics else float("nan")
        out[f"hit_{h}"] = float(np.mean(hits)) if hits else float("nan")
    return out


async def main() -> None:
    pool = await get_pool()
    log.info("Loading price rows...")
    df = await load_price_rows(pool)
    log.info("Building training sequences (may take 1–2 min)...")
    X, y, meta = build_sequences(df)
    log.info(f"Sequences: {X.shape}, Labels: {y.shape}")

    # Chronological split by sample time (no shuffle — avoids leakage)
    order = np.argsort(meta["time"].to_numpy(), kind="stable")
    X, y, meta = X[order], y[order], meta.iloc[order].reset_index(drop=True)
    split = int(len(X) * 0.8)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]
    meta_val = meta.iloc[split:].reset_index(drop=True)

    train_ds = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train))
    train_dl = DataLoader(train_ds, batch_size=64, shuffle=True)
    val_ds = TensorDataset(torch.from_numpy(X_val), torch.from_numpy(y_val))
    val_dl = DataLoader(val_ds, batch_size=256, shuffle=False)

    model = LSTMPredictor(input_size=len(PRICE_FEATURE_COLS), dropout=0.4)
    optimizer = torch.optim.Adam(model.parameters(), lr=3e-4, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=3
    )
    criterion = nn.HuberLoss()

    best_val_loss = float("inf")
    patience, patience_count = 5, 0

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
        val_loss = sum(val_losses) / len(val_losses)
        scheduler.step(val_loss)

        m = _epoch_metrics(model, X_val, meta_val)
        ic_str = " ".join(f"IC{h}={m[f'ic_{h}']:+.3f}" for h in HORIZONS)
        log.info(
            f"Epoch {epoch+1:02d} | train={train_loss:.4f} val={val_loss:.4f} | {ic_str}"
        )

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


async def dump(out_path: Path) -> None:
    pool = await get_pool()
    log.info("Loading price rows...")
    df = await load_price_rows(pool)
    log.info("Building sequences...")
    X, y, meta = build_sequences(df)
    np.savez_compressed(
        out_path,
        X=X,
        y=y,
        feature_names=np.array(PRICE_FEATURE_COLS),
        horizons=np.array(HORIZONS),
        meta_time=meta["time"].to_numpy().astype("datetime64[ns]"),
        meta_ticker=meta["ticker"].to_numpy().astype(str),
    )
    log.info(f"Saved → {out_path}  (X={X.shape}, y={y.shape})")
    log.info("Load with: d = np.load('dataset.npz', allow_pickle=True)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump", metavar="PATH", help="dump dataset to .npz and exit")
    args = parser.parse_args()

    if args.dump:
        asyncio.run(dump(Path(args.dump)))
    else:
        asyncio.run(main())
```

- [ ] **Step 2: Verify the module imports and is lint-clean**

Run: `python -c "import ml.train.train_lstm"` then `make lint` (or `ruff check ml/train/train_lstm.py`)
Expected: no import error, no lint errors.

- [ ] **Step 3: Run the full ml unit suite**

Run: `pytest tests/unit/ml/ -v`
Expected: PASS (all tasks 1–6 green).

- [ ] **Step 4: Commit**

```bash
git add ml/train/train_lstm.py
git commit -m "feat(ml): Huber loss, LR scheduler, rank-IC logging, updated dump"
```

---

## Task 8: Backtest harness

**Files:**
- Create: `ml/eval/backtest.py`
- Test: `tests/unit/ml/test_backtest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/ml/test_backtest.py
"""Tests for ml.eval.backtest."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _meta_with_preds():
    # 2 dates, 4 tickers each. raw_1 = realized 1d fwd return.
    rows = [
        # date d1
        {"time": "d1", "ticker": "A", "raw_1": 0.05, "pred": 3.0},
        {"time": "d1", "ticker": "B", "raw_1": 0.02, "pred": 2.0},
        {"time": "d1", "ticker": "C", "raw_1": -0.01, "pred": 1.0},
        {"time": "d1", "ticker": "D", "raw_1": -0.03, "pred": 0.0},
        # date d2
        {"time": "d2", "ticker": "A", "raw_1": -0.02, "pred": 0.0},
        {"time": "d2", "ticker": "B", "raw_1": 0.04, "pred": 3.0},
        {"time": "d2", "ticker": "C", "raw_1": 0.01, "pred": 2.0},
        {"time": "d2", "ticker": "D", "raw_1": -0.05, "pred": 1.0},
    ]
    return pd.DataFrame(rows)


def test_backtest_top_n_beats_market_when_pred_is_good():
    from ml.eval.backtest import run_backtest
    df = _meta_with_preds()
    res = run_backtest(df, pred_col="pred", ret_col="raw_1", top_n=2)
    # top-2 picks the higher-return names each date -> beats equal-weight mean
    assert res["strategy_cum_return"] > res["market_cum_return"]
    assert "sharpe" in res
    assert "max_drawdown" in res
    assert len(res["equity_curve"]) == 2


def test_backtest_handles_single_date():
    from ml.eval.backtest import run_backtest
    df = _meta_with_preds()
    df = df[df["time"] == "d1"]
    res = run_backtest(df, pred_col="pred", ret_col="raw_1", top_n=2)
    assert len(res["equity_curve"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/ml/test_backtest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ml.eval.backtest'`

- [ ] **Step 3: Implement the harness**

```python
# ml/eval/backtest.py
"""Top-N basket backtest over per-date predictions vs realized returns."""
from __future__ import annotations

import numpy as np
import pandas as pd


def run_backtest(
    df: pd.DataFrame,
    pred_col: str,
    ret_col: str,
    top_n: int,
) -> dict:
    """Long-only top-N basket, equal weight, one rebalance per date.

    Args:
        df: rows with columns [time, <pred_col>, <ret_col>]; `ret_col` is the
            realized forward return for the held horizon.
        pred_col: prediction column to rank by (descending).
        ret_col: realized return column for PnL.
        top_n: basket size per date.

    Returns:
        dict with per-date strategy/market returns, cumulative returns,
        Sharpe (per-period, unannualized), max drawdown, and the equity curve.
    """
    strat_rets: list[float] = []
    mkt_rets: list[float] = []
    dates: list = []

    for date, g in df.groupby("time", sort=True):
        g = g.dropna(subset=[pred_col, ret_col])
        if g.empty:
            continue
        k = min(top_n, len(g))
        top = g.nlargest(k, pred_col)
        strat_rets.append(float(top[ret_col].mean()))
        mkt_rets.append(float(g[ret_col].mean()))
        dates.append(date)

    strat = np.asarray(strat_rets, dtype=float)
    mkt = np.asarray(mkt_rets, dtype=float)

    equity = np.cumprod(1.0 + strat)
    market_equity = np.cumprod(1.0 + mkt)
    excess = strat - mkt
    sharpe = (
        float(excess.mean() / excess.std())
        if len(excess) > 1 and excess.std() > 0
        else float("nan")
    )
    peak = np.maximum.accumulate(equity) if len(equity) else np.array([1.0])
    drawdown = (equity / peak - 1.0) if len(equity) else np.array([0.0])
    max_dd = float(drawdown.min()) if len(drawdown) else 0.0

    return {
        "dates": dates,
        "strategy_returns": strat.tolist(),
        "market_returns": mkt.tolist(),
        "equity_curve": equity.tolist(),
        "market_equity_curve": market_equity.tolist(),
        "strategy_cum_return": float(equity[-1] - 1.0) if len(equity) else 0.0,
        "market_cum_return": float(market_equity[-1] - 1.0) if len(market_equity) else 0.0,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/ml/test_backtest.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add ml/eval/backtest.py tests/unit/ml/test_backtest.py
git commit -m "feat(ml): top-N basket backtest harness"
```

---

## Task 9: Adapt inference to the new model

**Files:**
- Modify: `ml/inference/predict_prices.py`

The new model needs all active tickers normalized together (cross-sectional), outputs regression rank-return, and `load` returns the model only. No new unit test (DB + model I/O bound); verification is import + lint + manual run.

- [ ] **Step 1: Rewrite `predict_prices.py`**

Replace the entire file with:

```python
"""
Run LSTM inference across all active tickers (cross-sectional) and write
ranked predictions to ml_predictions.

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
from ml.constants import HORIZONS, MIN_TICKERS_PER_DATE, PRICE_FEATURE_COLS, SEQ_LEN
from ml.features.cross_sectional import cross_sectional_zscore
from ml.features.feature_store import build_price_feature_matrix
from ml.models.lstm_predictor import LSTMPredictor

MODEL_PATH = Path("models/v1/lstm_v0.pt")
MODEL_VERSION = "lstm_v1"
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def _build_latest_panel(pool, tickers: list[str]) -> pd.DataFrame:
    """For each ticker, fetch the last SEQ_LEN+buffer days of features, tag with
    a per-day index so we can cross-sectionally z-score across tickers."""
    frames = []
    for tk in tickers:
        feat_df = await build_price_feature_matrix(pool, tk, lookback_days=SEQ_LEN + 30)
        avail = [c for c in PRICE_FEATURE_COLS if c in feat_df.columns]
        if feat_df.empty or len(feat_df) < SEQ_LEN or len(avail) < len(PRICE_FEATURE_COLS):
            continue
        g = feat_df.tail(SEQ_LEN).copy()
        g = g.reset_index().rename(columns={g.index.name or "index": "time"})
        if "time" not in g.columns:
            g = g.rename(columns={g.columns[0]: "time"})
        g["ticker"] = tk
        g["step"] = range(len(g))  # 0..SEQ_LEN-1, aligns dates across tickers
        frames.append(g[["step", "ticker", *PRICE_FEATURE_COLS]])
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


async def main() -> None:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"LSTM model not found at {MODEL_PATH}. Run ml.train.train_lstm first."
        )

    pool = await get_pool()
    model = LSTMPredictor.load(MODEL_PATH)
    model.eval()

    rows = await pool.fetch(
        "SELECT ticker FROM companies WHERE is_active = true ORDER BY ticker"
    )
    tickers = [r["ticker"] for r in rows]
    log.info(f"Building cross-sectional panel for {len(tickers)} tickers...")

    panel = await _build_latest_panel(pool, tickers)
    if panel.empty or panel["ticker"].nunique() < MIN_TICKERS_PER_DATE:
        log.warning("Insufficient tickers for cross-sectional inference; aborting.")
        return

    # Cross-sectional z-score per aligned step, then window per ticker.
    panel = cross_sectional_zscore(panel, PRICE_FEATURE_COLS, by="step")

    windows, kept = [], []
    for tk, g in panel.groupby("ticker", sort=False):
        g = g.sort_values("step")
        if len(g) < SEQ_LEN:
            continue
        windows.append(g[PRICE_FEATURE_COLS].tail(SEQ_LEN).to_numpy(np.float32))
        kept.append(tk)

    x = torch.from_numpy(np.stack(windows))           # (n_tickers, SEQ_LEN, n_feat)
    preds = model.predict(x).numpy()                  # (n_tickers, n_horizons)

    predicted_at = datetime.now(timezone.utc)
    prediction_date = predicted_at.date()

    for hi, horizon in enumerate(HORIZONS):
        scores = preds[:, hi]
        # cross-sectional percentile rank -> confidence in [0,1]
        pct = pd.Series(scores).rank(pct=True).to_numpy()
        for ti, ticker in enumerate(kept):
            direction = "up" if scores[ti] >= 0 else "down"
            confidence = float(pct[ti])
            await pool.execute(
                """
                INSERT INTO ml_predictions
                    (ticker, predicted_at, prediction_date, horizon_days,
                     predicted_direction, confidence, target_price, model_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (ticker, horizon_days, model_version, prediction_date) DO UPDATE
                    SET predicted_direction = EXCLUDED.predicted_direction,
                        confidence          = EXCLUDED.confidence,
                        target_price        = EXCLUDED.target_price,
                        predicted_at        = EXCLUDED.predicted_at
                """,
                ticker, predicted_at, prediction_date, horizon,
                direction, confidence, None, MODEL_VERSION,
            )

    log.info(f"Done. wrote {len(kept)} tickers × {len(HORIZONS)} horizons.")


if __name__ == "__main__":
    asyncio.run(main())
```

Note: `MODEL_VERSION` bumped to `lstm_v1`, `target_price` is now `None` (the model predicts relative rank, not a price level), and `confidence` is the cross-sectional percentile (high = stronger buy candidate).

- [ ] **Step 2: Verify import + lint**

Run: `python -c "import ml.inference.predict_prices"` then `ruff check ml/inference/predict_prices.py`
Expected: no import error, no lint errors.

- [ ] **Step 3: Grep for other callers of the changed API**

Run: `grep -rn "predict_proba\|\.load(MODEL_PATH)\|LSTMPredictor.load" ml/ mgmt/ extraction/`
Expected: only `ml/inference/predict_prices.py` (already updated). If any other file unpacks `model, scaler = ...load(...)` or calls `predict_proba`, fix it to `model = ...load(...)` / `.predict(...)`.

- [ ] **Step 4: Run full ml suite + typecheck**

Run: `pytest tests/unit/ml/ -v` then `make typecheck`
Expected: all PASS, mypy clean for `ml/`.

- [ ] **Step 5: Commit**

```bash
git add ml/inference/predict_prices.py
git commit -m "feat(ml): cross-sectional ranked inference for new LSTM"
```

---

## Task 10: End-to-end smoke verification (manual)

**Files:** none (operational verification against a running DB).

- [ ] **Step 1: Dump a dataset and sanity-check shapes**

Run: `python -m ml.train.train_lstm --dump /tmp/ds.npz`
Then:
```python
python -c "import numpy as np; d=np.load('/tmp/ds.npz', allow_pickle=True); print(d['X'].shape, d['y'].shape, d['horizons'])"
```
Expected: `X=(N,60,13)`, `y=(N,6)`, horizons `[1 2 3 5 8 13]`, N in the tens of thousands.

- [ ] **Step 2: Train**

Run: `python -m ml.train.train_lstm`
Expected: per-epoch log line shows decreasing `val` Huber loss and `IC*` values; at least one horizon (typically IC8/IC13) trends positive (> ~0.03). Model saved to `models/v1/lstm_v0.pt`.

- [ ] **Step 3: Backtest the validation predictions**

Add a short scratch run (or `python -m` snippet) that rebuilds val `meta` + preds and calls `run_backtest` per horizon; confirm `strategy_cum_return > market_cum_return` for the best horizon. (If no horizon beats market, capture the IC table — that is the signal to revisit features/horizons before wiring B's CNN front end.)

- [ ] **Step 4: Run inference**

Run: `python -m ml.inference.predict_prices`
Expected: `wrote N tickers × 6 horizons`; spot-check `ml_predictions` rows have `model_version='lstm_v1'`, `confidence` in [0,1].

---

## Self-Review Notes (author)

- **Spec coverage:** target/labels (Task 6), features+open (Task 2), two-stage norm (Tasks 3, 6), EMA (Task 6), attention+front-end slot (Task 5), 6 Fibonacci horizons (Task 1, threaded), scheduler+IC+hit metrics (Tasks 4, 7), backtest harness (Task 8), thin-date drop (Task 6). All spec sections mapped.
- **Acceptance criteria:** AC1/AC2 → Task 7 logging + Task 10.2; AC3 → Task 8 + Task 10.3; AC4 → cross-sectional norm in Tasks 3/6/9, no raw price fed; AC5 → Task 5 `front_end` slot + unchanged `forward` contract.
- **Type consistency:** `build_sequences` returns `(X, y, meta)` everywhere; `LSTMPredictor.load` returns model-only (Tasks 5, 9 aligned); `predict` (not `predict_proba`) used in Tasks 5, 7, 9; constant names match `ml/constants.py` across all tasks.
- **Out of scope (deferred per spec):** CNN front end (slot only), walk-forward validation, winsorization.
