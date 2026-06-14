# LSTM Per-Stock Direction + Confidence: Validation & Calibration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate the existing cross-sectional rank-return LSTM point-in-time, calibrate its raw output into an honest per-stock P(up), and make `predict_prices.py` ship that calibrated direction + confidence instead of a fake percentile rank.

**Architecture:** D0 extracts the shared sequence pipeline + trading-calendar helpers. D1 loads the trained model, rebuilds historical windows, and reports per-horizon rank-IC / directional hit-rate / calibration on a leak-free OOS slice (full vs ex-floor-price regime). D2 fits per-horizon isotonic calibrators on D1's OOS pairs (chronological sub-split). D3 rewires inference to emit calibrated probabilities; near-random horizons show ~0.5. D4 aligns the monitoring semantics.

**Tech Stack:** Python, asyncpg, PyTorch, pandas, NumPy, scikit-learn (`IsotonicRegression`), scipy (`binomtest`), pytest. Spec: `docs/superpowers/specs/2026-06-14-lstm-direction-calibration-design.md`.

---

## File Structure

- **Create** `ml/features/sequence_builder.py` — shared `load_price_rows`, `build_sequences`, plus pure calendar/split/purge helpers (`trading_calendar`, `date_boundary`, `advance_on_calendar`, `purged_val_start`).
- **Modify** `ml/train/train_lstm.py` — import `load_price_rows` / `build_sequences` from the new module; drop the local copies.
- **Modify** `ml/eval/metrics.py` — add `brier_score`, `reliability_table`, `directional_hit_rate`, `is_low_signal`.
- **Create** `ml/eval/lstm_validation.py` — the validation harness + an `oos_pairs()` function D2 reuses.
- **Create** `ml/eval/calibrate_direction.py` — fit + persist per-horizon isotonic calibrators.
- **Modify** `ml/inference/predict_prices.py` — extract a pure `calibrated_direction()` helper; load calibrators; bump `MODEL_VERSION`.
- **Modify** `ml/monitoring/accuracy_report.py` — clarifying comment; thresholds-revisit note.
- **Tests:** `tests/unit/ml/test_sequence_builder.py`, `test_eval_metrics.py`, `test_lstm_validation.py`, `test_calibrate_direction.py`, `test_predict_prices_confidence.py`.

Constants used throughout (define in `sequence_builder.py` or reuse `ml/constants.py`): `HORIZONS = [1,2,3,5,8,13]`, `SEQ_LEN = 60`, `MIN_TICKERS_PER_DATE = 20`. Floor regime constant lives in `lstm_validation.py`: `FLOOR_START = date(2022, 7, 28)`, `FLOOR_END = date(2024, 1, 22)`.

---

## D0 — Extract shared sequence pipeline + calendar helpers

### Task 1: Move `load_price_rows` + `build_sequences` into a shared module

**Files:**
- Create: `ml/features/sequence_builder.py`
- Modify: `ml/train/train_lstm.py` (remove local `load_price_rows`, `build_sequences`; import them)
- Test: `tests/unit/ml/test_sequence_builder.py`

- [ ] **Step 1: Create the shared module by moving the existing functions verbatim**

Cut `load_price_rows` (currently `train_lstm.py:50-67`) and `build_sequences` (`train_lstm.py:70-145`) into `ml/features/sequence_builder.py`. Keep their bodies byte-identical. Add the imports they need at the top of the new file:

```python
"""Shared price-sequence pipeline for training and evaluation.

Both ml.train.train_lstm and ml.eval.lstm_validation build windows the SAME way
(per-ticker clean_and_smooth -> forward returns -> cross-sectional z-score ->
build_windows) so served/evaluated features match training exactly. Keep this the
single source of truth (train/serve parity invariant).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ml.constants import (
    EMA_SPAN,
    HORIZONS,
    MIN_TICKERS_PER_DATE,
    PRICE_FEATURE_COLS,
    SEQ_LEN,
)
from ml.features.cross_sectional import (
    build_windows,
    clean_and_smooth,
    cross_sectional_zscore,
    forward_returns,
)
from ml.features.price_features import compute_price_features

LABEL_COLS = [f"y_{h}" for h in HORIZONS]
RAW_COLS = [f"fwd_ret_{h}" for h in HORIZONS]

# ... (moved load_price_rows and build_sequences here, unchanged) ...
```

- [ ] **Step 2: Re-wire `train_lstm.py` to import from the shared module**

In `ml/train/train_lstm.py`, delete the moved functions and the now-duplicated `LABEL_COLS` / `RAW_COLS`, and import:

```python
from ml.features.sequence_builder import (
    LABEL_COLS,
    RAW_COLS,
    build_sequences,
    load_price_rows,
)
```

Leave the rest of `train_lstm.py` (`main`, `dump`, `_epoch_metrics`) untouched.

- [ ] **Step 3: Verify nothing else imported the moved names**

Run: `grep -rn "from ml.train.train_lstm import" --include=*.py .`
Expected: no hits for `build_sequences` / `load_price_rows` (only `train_lstm` internal use). If any exist, repoint them to `ml.features.sequence_builder`.

- [ ] **Step 4: Smoke-import to confirm no circular import / NameError**

Run: `python -c "import ml.train.train_lstm; import ml.features.sequence_builder; print('ok')"`
Expected: prints `ok` (no import error).

- [ ] **Step 5: Commit**

```bash
git add ml/features/sequence_builder.py ml/train/train_lstm.py
git commit -m "refactor(ml): extract build_sequences/load_price_rows to shared sequence_builder

Single source of truth for the price-sequence pipeline so train and the new
eval harness build windows identically (train/serve parity)."
```

---

### Task 2: Add pure trading-calendar + leak-free split helpers

**Files:**
- Modify: `ml/features/sequence_builder.py`
- Test: `tests/unit/ml/test_sequence_builder.py`

- [ ] **Step 1: Write failing tests for the calendar/split/purge helpers**

Create `tests/unit/ml/test_sequence_builder.py`:

```python
import numpy as np
import pandas as pd

from ml.features.sequence_builder import (
    advance_on_calendar,
    date_boundary,
    purged_val_start,
    trading_calendar,
)


def test_trading_calendar_is_sorted_unique_union():
    times = np.array(
        ["2024-01-03", "2024-01-01", "2024-01-03", "2024-01-02"],
        dtype="datetime64[ns]",
    )
    cal = trading_calendar(times)
    assert list(cal) == list(pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]))


def test_date_boundary_splits_by_unique_date_quantile():
    # 10 unique dates; frac=0.8 -> boundary is the 9th (index 8)
    dates = pd.to_datetime([f"2024-01-{d:02d}" for d in range(1, 11)])
    times = np.array(list(dates) + list(dates))  # each date appears twice
    b = date_boundary(times, frac=0.8)
    assert b == dates[8]


def test_advance_on_calendar_moves_n_trading_bars_not_calendar_days():
    # Mon-Fri only (skips weekend) -> advancing 3 bars from Fri lands on Wed
    cal = trading_calendar(
        pd.to_datetime(
            ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
             "2024-01-08", "2024-01-09", "2024-01-10"]  # note: weekend 6-7 absent
        ).to_numpy()
    )
    out = advance_on_calendar(cal, pd.Timestamp("2024-01-05"), 3)
    assert out == pd.Timestamp("2024-01-10")


def test_advance_on_calendar_clamps_past_end():
    cal = trading_calendar(pd.to_datetime(["2024-01-01", "2024-01-02"]).to_numpy())
    assert advance_on_calendar(cal, pd.Timestamp("2024-01-02"), 5) == pd.Timestamp("2024-01-02")


def test_purged_val_start_advances_from_last_train_date():
    cal = trading_calendar(
        pd.to_datetime([f"2024-01-{d:02d}" for d in range(1, 21)]).to_numpy()
    )
    boundary = pd.Timestamp("2024-01-15")  # train = dates < 15, last train = 14
    # gap of 3 bars from 2024-01-14 -> 2024-01-17
    assert purged_val_start(cal, boundary, gap_bars=3) == pd.Timestamp("2024-01-17")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/ml/test_sequence_builder.py -v`
Expected: FAIL — `ImportError: cannot import name 'trading_calendar'`.

- [ ] **Step 3: Implement the helpers in `sequence_builder.py`**

Append to `ml/features/sequence_builder.py`:

```python
def trading_calendar(times: np.ndarray) -> pd.DatetimeIndex:
    """Sorted, de-duplicated union of all sample dates — the master trading
    calendar. DSE trades Sun-Thu with holidays, so calendar gaps are irregular;
    using actual observed dates (not a fixed frequency) is the only correct base
    for purging by trading bars."""
    return pd.DatetimeIndex(pd.to_datetime(np.unique(times))).sort_values()


def date_boundary(times: np.ndarray, frac: float) -> pd.Timestamp:
    """Train/val cut on a DATE boundary (not a sample-index boundary), so no
    single date straddles train and val. Returns the date at the `frac` quantile
    of unique sorted dates; callers assign dates < boundary to train, >= to val."""
    uniq = pd.DatetimeIndex(pd.to_datetime(np.unique(times))).sort_values()
    idx = min(int(len(uniq) * frac), len(uniq) - 1)
    return uniq[idx]


def advance_on_calendar(
    calendar: pd.DatetimeIndex, start: pd.Timestamp, n_bars: int
) -> pd.Timestamp:
    """Advance `n_bars` trading bars forward from `start` on the calendar.
    Clamps to the last calendar date if it runs off the end."""
    pos = int(calendar.searchsorted(start, side="left"))
    return calendar[min(pos + n_bars, len(calendar) - 1)]


def purged_val_start(
    calendar: pd.DatetimeIndex, boundary: pd.Timestamp, gap_bars: int
) -> pd.Timestamp:
    """First val date kept after purging. Train labels look forward up to
    gap_bars from the last train date, so val samples earlier than that overlap
    train labels and must be dropped. last_train_date = largest date < boundary;
    advance gap_bars trading bars from it."""
    train_dates = calendar[calendar < boundary]
    if len(train_dates) == 0:
        return boundary
    return advance_on_calendar(calendar, train_dates[-1], gap_bars)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/ml/test_sequence_builder.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/features/sequence_builder.py tests/unit/ml/test_sequence_builder.py
git commit -m "feat(ml): trading-calendar split + purge helpers for leak-free OOS eval"
```

---

## D1 — Validation harness

### Task 3: Add eval metrics (Brier, reliability, hit-rate, low-signal)

**Files:**
- Modify: `ml/eval/metrics.py`
- Test: `tests/unit/ml/test_eval_metrics.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/ml/test_eval_metrics.py`:

```python
import numpy as np

from ml.eval.metrics import (
    brier_score,
    directional_hit_rate,
    is_low_signal,
    reliability_table,
)


def test_brier_score_perfect_is_zero():
    prob = np.array([1.0, 0.0, 1.0, 0.0])
    outcome = np.array([1, 0, 1, 0])
    assert brier_score(prob, outcome) == 0.0


def test_brier_score_known_value():
    # (0.7-1)^2 + (0.2-0)^2 = 0.09 + 0.04 = 0.13; mean = 0.065
    assert abs(brier_score(np.array([0.7, 0.2]), np.array([1, 0])) - 0.065) < 1e-9


def test_directional_hit_rate_counts_sign_agreement():
    pred = np.array([0.5, -0.2, 0.1, -3.0])   # up, down, up, down
    actual_up = np.array([1, 0, 0, 1])          # hit, hit, miss, miss
    assert directional_hit_rate(pred, actual_up) == 0.5


def test_reliability_table_bins_and_counts():
    prob = np.array([0.05, 0.15, 0.95, 0.85])
    outcome = np.array([0, 0, 1, 1])
    tbl = reliability_table(prob, outcome, n_bins=2)
    # bin 0 = [0,0.5): two samples, frac_pos 0; bin 1 = [0.5,1]: two, frac_pos 1
    assert list(tbl["n"]) == [2, 2]
    assert list(tbl["frac_pos"]) == [0.0, 1.0]


def test_is_low_signal_true_at_base_rate():
    # hit == base, large n -> not distinguishable -> low signal
    assert is_low_signal(hit_rate=0.50, base_rate=0.50, n=500) is True


def test_is_low_signal_false_with_clear_edge():
    # +8pp over base, large n -> real signal
    assert is_low_signal(hit_rate=0.58, base_rate=0.50, n=500) is False


def test_is_low_signal_true_when_effect_below_2pp_even_if_significant():
    # tiny 1pp edge: effect-size floor flags it regardless of n
    assert is_low_signal(hit_rate=0.51, base_rate=0.50, n=100_000) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/ml/test_eval_metrics.py -v`
Expected: FAIL — `ImportError: cannot import name 'brier_score'`.

- [ ] **Step 3: Implement the metrics**

Append to `ml/eval/metrics.py`:

```python
def brier_score(prob: np.ndarray, outcome: np.ndarray) -> float:
    """Mean squared error between predicted P(up) and the 0/1 outcome."""
    prob = np.asarray(prob, dtype=float)
    outcome = np.asarray(outcome, dtype=float)
    return float(np.mean((prob - outcome) ** 2))


def directional_hit_rate(pred: np.ndarray, actual_up: np.ndarray) -> float:
    """Fraction where the predicted up/down call (pred >= 0 == 'up') matches the
    realized direction (actual_up is 1 if the forward return was > 0)."""
    pred = np.asarray(pred, dtype=float)
    actual_up = np.asarray(actual_up, dtype=int)
    call_up = (pred >= 0).astype(int)
    return float((call_up == actual_up).mean())


def reliability_table(prob: np.ndarray, outcome: np.ndarray, n_bins: int = 10):
    """Per-bin mean predicted prob vs realized frequency — the calibration curve.
    Returns a DataFrame with columns [bin, mean_prob, frac_pos, n]; empty bins
    are dropped."""
    import pandas as pd

    prob = np.asarray(prob, dtype=float)
    outcome = np.asarray(outcome, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    # clip to [0,1) so prob==1.0 lands in the last bin
    idx = np.clip(np.digitize(prob, edges[1:-1], right=False), 0, n_bins - 1)
    rows = []
    for b in range(n_bins):
        m = idx == b
        if not m.any():
            continue
        rows.append(
            {
                "bin": b,
                "mean_prob": float(prob[m].mean()),
                "frac_pos": float(outcome[m].mean()),
                "n": int(m.sum()),
            }
        )
    return pd.DataFrame(rows)


def is_low_signal(hit_rate: float, base_rate: float, n: int) -> bool:
    """A horizon is 'low signal' if its directional edge over the base rate is
    not worth shipping. Flagged (True) if EITHER the one-sided binomial test
    cannot reject 'hit == base' at p>0.10, OR the effect size is < 2pp. OR-combined
    so it suppresses conservatively (overlapping forward windows inflate the
    binomial significance, so the effect-size floor is the real guard)."""
    from scipy.stats import binomtest

    if abs(hit_rate - base_rate) < 0.02:
        return True
    successes = int(round(hit_rate * n))
    p = binomtest(successes, n, base_rate, alternative="greater").pvalue
    return bool(p > 0.10)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/ml/test_eval_metrics.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/eval/metrics.py tests/unit/ml/test_eval_metrics.py
git commit -m "feat(ml): Brier, reliability table, directional hit-rate, low-signal test"
```

---

### Task 4: Build the validation harness with a testable core

**Files:**
- Create: `ml/eval/lstm_validation.py`
- Test: `tests/unit/ml/test_lstm_validation.py`

The DB + model parts (`load`, `predict`) are thin I/O wrappers. All scoring logic lives in pure functions that take an already-assembled long results DataFrame, so they are unit-testable offline.

- [ ] **Step 1: Write failing tests for the pure scoring core**

Create `tests/unit/ml/test_lstm_validation.py`:

```python
from datetime import date

import numpy as np
import pandas as pd

from ml.eval.lstm_validation import (
    evaluate_results,
    select_oos,
    split_regime,
)


def _results_df(n_dates=20, n_tickers=30, seed=0):
    """Long results frame: one row per (time, ticker), with a single horizon's
    pred score and raw forward return. pred is correlated with fwd so signal exists."""
    rng = np.random.default_rng(seed)
    dates = pd.to_datetime([f"2021-{1 + d // 28:02d}-{1 + d % 28:02d}" for d in range(n_dates)])
    rows = []
    for t in dates:
        fwd = rng.normal(0, 0.05, n_tickers)
        pred = fwd + rng.normal(0, 0.02, n_tickers)  # correlated -> positive IC
        for i in range(n_tickers):
            rows.append({"time": t, "ticker": f"T{i}", "pred_5": pred[i], "fwd_ret_5": fwd[i]})
    return pd.DataFrame(rows)


def test_select_oos_keeps_only_purged_val_tail():
    df = _results_df()
    oos = select_oos(df, frac=0.8, gap_bars=2)
    # everything kept is at/after the purged boundary, and it's a strict subset
    assert len(oos) < len(df)
    assert oos["time"].min() > df["time"].min()


def test_split_regime_partitions_on_floor_window():
    df = _results_df()
    # inject a few rows inside the floor window
    floor_rows = df.head(3).copy()
    floor_rows["time"] = pd.Timestamp("2022-09-01")
    df2 = pd.concat([df, floor_rows], ignore_index=True)
    ex_floor = split_regime(df2, floor_start=date(2022, 7, 28), floor_end=date(2024, 1, 22))
    assert (ex_floor["time"] < pd.Timestamp("2022-07-28")).all() or (
        ex_floor["time"] > pd.Timestamp("2024-01-22")
    ).all()
    assert len(ex_floor) == len(df)  # the 3 floor rows removed


def test_evaluate_results_reports_positive_ic_for_correlated_signal():
    df = _results_df(seed=1)
    rep = evaluate_results(df, horizons=[5])
    blk = rep["horizons"][5]
    assert blk["rank_ic"]["mean_ic"] > 0.3        # strong correlation
    assert blk["hit_rate"] > 0.5
    assert "base_rate" in blk and "brier" in blk
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/ml/test_lstm_validation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml.eval.lstm_validation'`.

- [ ] **Step 3: Implement `lstm_validation.py`**

Create `ml/eval/lstm_validation.py`:

```python
"""Point-in-time validation of the cross-sectional rank-return LSTM, reframed for
the per-stock direction + confidence the UI ships.

For each horizon, on a leak-free out-of-sample slice, reports three framings:
  1. rank-IC        — does the relative ranking signal exist? (model's design)
  2. directional hit-rate vs base rate — is the absolute up/down call > 50%?
  3. calibration    — Brier + reliability of the to-be-shipped confidence.
Split full vs ex-floor-price regime (2022-07..2024-01), whose returns were frozen
by BSEC and poison return-based signal.

Run: python -m ml.eval.lstm_validation
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from db.pool import get_batch_pool
from ml.constants import HORIZONS, SEQ_LEN
from ml.eval.metrics import (
    brier_score,
    directional_hit_rate,
    rank_ic,
    reliability_table,
)
from ml.features.sequence_builder import (
    build_sequences,
    date_boundary,
    load_price_rows,
    purged_val_start,
    trading_calendar,
)
from ml.models.lstm_predictor import LSTMPredictor

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

MODEL_PATH = Path("models/v1/lstm_v0.pt")
FLOOR_START = date(2022, 7, 28)
FLOOR_END = date(2024, 1, 22)
SPLIT_FRAC = 0.8


def select_oos(results: pd.DataFrame, frac: float, gap_bars: int) -> pd.DataFrame:
    """Keep only the purged validation tail: dates >= purged_val_start, where the
    train/val cut is a date boundary and the purge advances gap_bars trading bars."""
    times = results["time"].to_numpy()
    cal = trading_calendar(times)
    boundary = date_boundary(times, frac)
    start = purged_val_start(cal, boundary, gap_bars)
    return results[results["time"] >= start].reset_index(drop=True)


def split_regime(results: pd.DataFrame, floor_start: date, floor_end: date) -> pd.DataFrame:
    """Drop samples whose entry date falls inside the floor-price window. (Entry
    date is a conservative proxy for forward-window overlap; documented in spec.)"""
    t = results["time"]
    keep = (t < pd.Timestamp(floor_start)) | (t > pd.Timestamp(floor_end))
    return results[keep].reset_index(drop=True)


def evaluate_results(results: pd.DataFrame, horizons: list[int]) -> dict:
    """Per-horizon rank-IC (per date, averaged), directional hit-rate vs base
    rate, and calibration (Brier + reliability) over the given long results frame."""
    out: dict = {"n_samples": int(len(results)), "horizons": {}}
    for h in horizons:
        pcol, fcol = f"pred_{h}", f"fwd_ret_{h}"
        sub = results.dropna(subset=[pcol, fcol])
        if sub.empty:
            continue
        ics = [
            rank_ic(g[pcol].to_numpy(), g[fcol].to_numpy())
            for _, g in sub.groupby("time")
        ]
        ics = [v for v in ics if np.isfinite(v)]
        actual_up = (sub[fcol].to_numpy() > 0).astype(int)
        hit = directional_hit_rate(sub[pcol].to_numpy(), actual_up)
        base = float(actual_up.mean())
        # confidence proxy = min-max of pred into [0,1] for a pre-calibration Brier read
        s = sub[pcol].to_numpy()
        rng = s.max() - s.min()
        conf = (s - s.min()) / rng if rng > 0 else np.full_like(s, 0.5)
        out["horizons"][h] = {
            "rank_ic": {
                "n": len(ics),
                "mean_ic": round(float(np.mean(ics)), 4) if ics else float("nan"),
                "pct_positive": round(float(np.mean(np.array(ics) > 0)), 3) if ics else float("nan"),
            },
            "hit_rate": round(hit, 4),
            "base_rate": round(base, 4),
            "brier": round(brier_score(conf, actual_up), 4),
            "reliability": reliability_table(conf, actual_up).to_dict("records"),
        }
    return out


async def _build_results(pool) -> pd.DataFrame:
    """Load model + all historical windows, run inference, return a long results
    frame [time, ticker, pred_h..., fwd_ret_h...]."""
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"No model at {MODEL_PATH}. Run `python -m ml.train.train_lstm` first."
        )
    df = await load_price_rows(pool)
    X, _y, meta = build_sequences(df)  # noqa: N806
    model = LSTMPredictor.load(MODEL_PATH)
    preds = model.predict(torch.from_numpy(X)).numpy()  # (N, n_horizons)
    res = meta[["time", "ticker"]].copy()
    res["time"] = pd.to_datetime(res["time"])
    for hi, h in enumerate(HORIZONS):
        res[f"pred_{h}"] = preds[:, hi]
        res[f"fwd_ret_{h}"] = meta[f"fwd_ret_{h}"].to_numpy()
    return res


def oos_pairs(results_oos: pd.DataFrame, horizons: list[int]) -> dict[int, pd.DataFrame]:
    """Per-horizon [time, score, actual_up] frames for the calibrator (D2)."""
    pairs: dict[int, pd.DataFrame] = {}
    for h in horizons:
        pcol, fcol = f"pred_{h}", f"fwd_ret_{h}"
        sub = results_oos.dropna(subset=[pcol, fcol])
        pairs[h] = pd.DataFrame(
            {
                "time": sub["time"].to_numpy(),
                "score": sub[pcol].to_numpy(),
                "actual_up": (sub[fcol].to_numpy() > 0).astype(int),
            }
        )
    return pairs


def _print_report(tag: str, rep: dict) -> None:
    log.info("=" * 64)
    log.info("LSTM DIRECTION VALIDATION [%s]  n=%d", tag, rep["n_samples"])
    for h, blk in rep["horizons"].items():
        log.info(
            "  h=%2dd  IC=%-7s pos%%=%-5s | hit=%.3f base=%.3f | Brier=%.4f",
            h, blk["rank_ic"]["mean_ic"], blk["rank_ic"]["pct_positive"],
            blk["hit_rate"], blk["base_rate"], blk["brier"],
        )


async def main() -> dict:
    pool = await get_batch_pool()
    results = await _build_results(pool)
    log.info("results rows=%d (survivorship: all tickers with history)", len(results))
    oos = select_oos(results, SPLIT_FRAC, gap_bars=max(HORIZONS))
    full = evaluate_results(oos, HORIZONS)
    ex_floor = evaluate_results(split_regime(oos, FLOOR_START, FLOOR_END), HORIZONS)
    _print_report("full", full)
    _print_report("ex_floor", ex_floor)
    return {"full": full, "ex_floor": ex_floor}


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/ml/test_lstm_validation.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/eval/lstm_validation.py tests/unit/ml/test_lstm_validation.py
git commit -m "feat(ml): LSTM direction validation harness (rank-IC/hit-rate/calibration, ex-floor)"
```

---

## D2 — Direction calibrator

### Task 5: Fit + persist per-horizon isotonic calibrators

**Files:**
- Create: `ml/eval/calibrate_direction.py`
- Test: `tests/unit/ml/test_calibrate_direction.py`

- [ ] **Step 1: Write failing tests for the pure fit/eval core**

Create `tests/unit/ml/test_calibrate_direction.py`:

```python
import numpy as np
import pandas as pd

from ml.eval.calibrate_direction import fit_horizon_calibrator


def _pairs(n=400, signal=True, seed=0):
    rng = np.random.default_rng(seed)
    t = pd.to_datetime("2023-01-01") + pd.to_timedelta(np.arange(n), unit="D")
    score = rng.normal(0, 1, n)
    if signal:
        p = 1 / (1 + np.exp(-score))            # score truly drives P(up)
        up = (rng.random(n) < p).astype(int)
    else:
        up = (rng.random(n) < 0.5).astype(int)  # independent of score
    return pd.DataFrame({"time": t, "score": score, "actual_up": up})


def test_calibrator_is_monotonic_nondecreasing():
    iso, meta = fit_horizon_calibrator(_pairs(signal=True))
    grid = np.linspace(-3, 3, 50)
    out = iso.predict(grid)
    assert np.all(np.diff(out) >= -1e-9)        # isotonic: never decreasing


def test_calibrator_real_signal_not_low_and_improves_brier():
    iso, meta = fit_horizon_calibrator(_pairs(signal=True, seed=2))
    assert meta["low_signal"] is False
    assert meta["brier"] < 0.25                  # better than the 0.25 coin-flip floor


def test_calibrator_no_signal_flagged_low():
    iso, meta = fit_horizon_calibrator(_pairs(signal=False, seed=3))
    assert meta["low_signal"] is True


def test_fit_uses_chronological_subsplit_no_overlap():
    # eval half must be strictly later than fit half
    iso, meta = fit_horizon_calibrator(_pairs(signal=True))
    assert pd.Timestamp(meta["fit_end"]) <= pd.Timestamp(meta["eval_start"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/ml/test_calibrate_direction.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml.eval.calibrate_direction'`.

- [ ] **Step 3: Implement `calibrate_direction.py`**

Create `ml/eval/calibrate_direction.py`:

```python
"""Fit per-horizon isotonic calibrators mapping the LSTM's raw score -> P(up).

Consumes the D1 out-of-sample (score, actual_up) pairs ONLY (never train data),
sub-split chronologically: fit on the earlier half, evaluate Brier/reliability/
low-signal on the later half. Persists {horizon: IsotonicRegression} + metadata.

Run: python -m ml.eval.calibrate_direction
"""
from __future__ import annotations

import asyncio
import logging
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from ml.constants import HORIZONS
from ml.eval.lstm_validation import (
    SPLIT_FRAC,
    _build_results,
    oos_pairs,
    select_oos,
)
from ml.eval.metrics import brier_score, directional_hit_rate, is_low_signal, reliability_table

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

CALIBRATORS_PATH = Path("models/v1/lstm_direction_calibrators.pkl")


def fit_horizon_calibrator(pairs: pd.DataFrame) -> tuple[IsotonicRegression, dict]:
    """Chronological 50/50 sub-split of one horizon's OOS pairs. Fit isotonic on
    the earlier half (score -> actual_up), evaluate on the later half. Returns the
    fitted calibrator and a metadata dict (Brier, base/hit rate, low_signal,
    date ranges)."""
    pairs = pairs.sort_values("time").reset_index(drop=True)
    mid = len(pairs) // 2
    fit, ev = pairs.iloc[:mid], pairs.iloc[mid:]

    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(fit["score"].to_numpy(), fit["actual_up"].to_numpy())

    p_up = iso.predict(ev["score"].to_numpy())
    actual = ev["actual_up"].to_numpy()
    base = float(actual.mean())
    # directional_hit_rate expects a signed score; calibrated p>=0.5 == 'up'
    hit = directional_hit_rate(p_up - 0.5, actual)
    meta = {
        "n_fit": int(len(fit)),
        "n_eval": int(len(ev)),
        "base_rate": round(base, 4),
        "hit_rate": round(hit, 4),
        "brier": round(brier_score(p_up, actual), 4),
        "low_signal": is_low_signal(hit, base, len(ev)),
        "reliability": reliability_table(p_up, actual).to_dict("records"),
        "fit_end": str(fit["time"].max()),
        "eval_start": str(ev["time"].min()),
    }
    return iso, meta


async def main() -> dict:
    from db.pool import get_batch_pool

    pool = await get_batch_pool()
    results = await _build_results(pool)
    oos = select_oos(results, SPLIT_FRAC, gap_bars=max(HORIZONS))
    pairs_by_h = oos_pairs(oos, HORIZONS)

    calibrators: dict[int, IsotonicRegression] = {}
    metadata: dict[int, dict] = {}
    for h in HORIZONS:
        pairs = pairs_by_h[h]
        if len(pairs) < 100:
            log.warning("h=%dd: only %d OOS pairs — skipping", h, len(pairs))
            continue
        iso, meta = fit_horizon_calibrator(pairs)
        calibrators[h] = iso
        metadata[h] = meta
        log.info(
            "h=%2dd  Brier=%.4f hit=%.3f base=%.3f low_signal=%s",
            h, meta["brier"], meta["hit_rate"], meta["base_rate"], meta["low_signal"],
        )

    CALIBRATORS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CALIBRATORS_PATH.open("wb") as f:
        pickle.dump({"calibrators": calibrators, "metadata": metadata}, f)
    log.info("saved -> %s", CALIBRATORS_PATH)
    return metadata


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/ml/test_calibrate_direction.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/eval/calibrate_direction.py tests/unit/ml/test_calibrate_direction.py
git commit -m "feat(ml): per-horizon isotonic direction calibrators on OOS pairs"
```

---

## D3 — Honest inference

### Task 6: Extract a pure `calibrated_direction` helper and rewire `predict_prices.py`

**Files:**
- Modify: `ml/inference/predict_prices.py`
- Test: `tests/unit/ml/test_predict_prices_confidence.py`

- [ ] **Step 1: Write failing tests for the pure helper**

Create `tests/unit/ml/test_predict_prices_confidence.py`:

```python
import numpy as np
from sklearn.isotonic import IsotonicRegression

from ml.inference.predict_prices import calibrated_direction


def _fitted_iso():
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    # higher score -> higher P(up)
    iso.fit(np.array([-2.0, -1.0, 0.0, 1.0, 2.0]), np.array([0, 0, 0, 1, 1]))
    return iso


def test_confidence_is_calibrated_prob_not_percentile():
    iso = _fitted_iso()
    scores = np.array([2.0, -2.0])
    dirs, confs = calibrated_direction(scores, iso, low_signal=False)
    assert dirs[0] == "up" and confs[0] > 0.5
    assert dirs[1] == "down" and confs[1] > 0.5        # confidence of the DOWN call
    # not a rank: the top score's confidence is the prob, well below 1.0 here
    assert confs[0] < 1.0


def test_confidence_for_down_call_is_one_minus_p():
    iso = _fitted_iso()
    dirs, confs = calibrated_direction(np.array([-2.0]), iso, low_signal=False)
    p_up = float(iso.predict([-2.0])[0])
    assert dirs[0] == "down"
    assert abs(confs[0] - (1 - p_up)) < 1e-9


def test_low_signal_writes_neutral_half():
    iso = _fitted_iso()
    dirs, confs = calibrated_direction(np.array([2.0, -2.0]), iso, low_signal=True)
    assert list(confs) == [0.5, 0.5]


def test_no_calibrator_writes_neutral_half():
    dirs, confs = calibrated_direction(np.array([2.0, -2.0]), None, low_signal=False)
    assert list(confs) == [0.5, 0.5]
    assert dirs[0] == "up" and dirs[1] == "down"       # direction still by sign
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/ml/test_predict_prices_confidence.py -v`
Expected: FAIL — `ImportError: cannot import name 'calibrated_direction'`.

- [ ] **Step 3: Add the helper + calibrator loading; replace the percentile path**

In `ml/inference/predict_prices.py`:

(a) Add imports near the top:

```python
import pickle
```

(b) Change the version constant and add the calibrators path (replace `MODEL_VERSION = "lstm_v1"`):

```python
MODEL_VERSION = "lstm_v2_cal"
CALIBRATORS_PATH = Path("models/v1/lstm_direction_calibrators.pkl")
```

(c) Add the pure helper and a loader above `main()`:

```python
def calibrated_direction(scores, calibrator, low_signal: bool):
    """Map raw per-ticker scores for one horizon to (directions, confidences).

    With a usable calibrator: confidence = calibrated P of the STATED side
    (P(up) for an 'up' call, 1-P(up) for 'down'). Without one, or when the
    horizon is low-signal, confidence is a neutral 0.5 — honest 'no conviction' —
    while direction still follows the sign of the score.
    """
    scores = np.asarray(scores, dtype=float)
    directions = ["up" if s >= 0 else "down" for s in scores]
    if calibrator is None or low_signal:
        return directions, np.full(len(scores), 0.5)
    p_up = np.clip(calibrator.predict(scores), 0.0, 1.0)
    directions = ["up" if p >= 0.5 else "down" for p in p_up]
    confidences = np.where(p_up >= 0.5, p_up, 1.0 - p_up)
    return directions, confidences


def _load_calibrators():
    """Returns (calibrators dict, metadata dict). Empty if the file is absent —
    inference then writes neutral confidence everywhere (honest degraded mode)."""
    if not CALIBRATORS_PATH.exists():
        log.warning("No calibrators at %s — writing neutral 0.5 confidence.", CALIBRATORS_PATH)
        return {}, {}
    with CALIBRATORS_PATH.open("rb") as f:
        blob = pickle.load(f)
    return blob.get("calibrators", {}), blob.get("metadata", {})
```

(d) Replace the per-horizon write loop (`predict_prices.py:89-110`) so confidence comes from the helper. New loop:

```python
    calibrators, cal_meta = _load_calibrators()

    predicted_at = datetime.now(UTC)
    prediction_date = predicted_at.date()

    for hi, horizon in enumerate(HORIZONS):
        scores = preds[:, hi]
        iso = calibrators.get(horizon)
        low_signal = bool(cal_meta.get(horizon, {}).get("low_signal", False))
        directions, confidences = calibrated_direction(scores, iso, low_signal)
        for ti, ticker in enumerate(kept):
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
                directions[ti], float(confidences[ti]), None, MODEL_VERSION,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/ml/test_predict_prices_confidence.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Confirm the percentile path is gone**

Run: `grep -n "rank(pct=True)" ml/inference/predict_prices.py`
Expected: no output (the fake-confidence line is removed).

- [ ] **Step 6: Commit**

```bash
git add ml/inference/predict_prices.py tests/unit/ml/test_predict_prices_confidence.py
git commit -m "feat(ml): ship calibrated P(up) as confidence; bump model_version to lstm_v2_cal

Replaces the percentile-rank-as-confidence lie. Low-signal / uncalibrated
horizons write a neutral 0.5 (honest no-conviction). New model_version means a
fresh prediction lineage; the API serves newest predicted_at per horizon, so
calibrated rows win on the next run."
```

---

## D4 — Monitoring alignment

### Task 7: Document semantics + thresholds-revisit in `accuracy_report.py`

**Files:**
- Modify: `ml/monitoring/accuracy_report.py`

No behavior change: once `predicted_direction` is an absolute up/down call (D3), the existing `actual_direction = up if h_price > p_price` comparison is already correct. Record that and flag the pre-signal thresholds for revisit after D1.

- [ ] **Step 1: Add a clarifying comment at the `correct` computation**

In `populate_outcomes`, right after `actual_direction = "up" if h_price > p_price else "down"` (`accuracy_report.py:65`), add:

```python
        # As of model_version lstm_v2_cal, predicted_direction is an ABSOLUTE
        # up/down call (from the calibrated P(up)), so comparing it to the
        # absolute realized direction here is apples-to-apples. (Pre-lstm_v2_cal
        # rows encoded a RELATIVE 'beats peers' call and this comparison was
        # mismatched — see spec 2026-06-14-lstm-direction-calibration.)
```

- [ ] **Step 2: Add a thresholds-revisit note on `check_accuracy_thresholds`**

In the docstring of `check_accuracy_thresholds` (`accuracy_report.py:130`), append:

```python
    NOTE: warning=0.48 / critical=0.45 assume a ~50% base rate. The D1 validation
    reports the real per-horizon DSE up-fraction; once known, re-set these per
    horizon relative to that base rate rather than the flat defaults.
```

- [ ] **Step 3: Confirm the module still imports**

Run: `python -c "import ml.monitoring.accuracy_report; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git add ml/monitoring/accuracy_report.py
git commit -m "docs(ml): note absolute-direction semantics + thresholds revisit in accuracy_report"
```

---

## Final verification

### Task 8: Full suite + lint/typecheck

- [ ] **Step 1: Run all new unit tests together**

Run: `pytest tests/unit/ml/test_sequence_builder.py tests/unit/ml/test_eval_metrics.py tests/unit/ml/test_lstm_validation.py tests/unit/ml/test_calibrate_direction.py tests/unit/ml/test_predict_prices_confidence.py -v`
Expected: all PASS (23 tests).

- [ ] **Step 2: Run the broader unit suite to catch regressions from the D0 refactor**

Run: `make test`
Expected: no NEW failures. (Per memory, ~5 pre-existing `test_fundamental_scorer.py` failures are unrelated and predate this work — confirm the count is unchanged, not increased.)

- [ ] **Step 3: Lint + typecheck**

Run: `make check`
Expected: clean (ruff + mypy). Fix any issues this work introduced.

- [ ] **Step 4: Commit any lint/type fixes**

```bash
git add -A
git commit -m "chore(ml): lint/type fixes for direction-calibration work"
```

---

## Live run (manual — requires populated TimescaleDB; not part of CI)

Documented for the operator; not an automated task:

```bash
make up                                      # start DB/redis stack
python -m ml.train.train_lstm                # produce models/v1/lstm_v0.pt
python -m ml.eval.lstm_validation            # READ THE NUMBERS — full vs ex_floor
python -m ml.eval.calibrate_direction        # writes lstm_direction_calibrators.pkl
python -m ml.inference.predict_prices        # writes calibrated lstm_v2_cal rows
# optional cleanup of the old fabricated lineage:
#   DELETE FROM ml_predictions WHERE model_version = 'lstm_v1';
```

**Decision gate after `lstm_validation`:** if `ex_floor` rank-IC AND hit-rate are both ≈ base rate, the model is poisoned by floor-regime training labels — the next step is to **retrain excluding the floor window** (scoped move toward Approach B), NOT to trust the calibrator. If `ex_floor` shows real edge but `full` is muddy, proceed with calibration and note regime sensitivity. (See spec "Decision rules (post-D1)".)
