# LSTM Ranking Model Redesign

**Date:** 2026-06-05
**Status:** Approved design, pending implementation plan
**Component:** `ml/` — price direction predictor → cross-sectional return-ranking model

## Problem

The current `LSTMPredictor` (`ml/models/lstm_predictor.py`) trained by `ml/train/train_lstm.py`
underperforms: validation direction accuracy sits near 50% (coin-flip) and a trading backtest on
its predictions loses money. The model learns no tradeable signal.

Root causes are not architectural. They are:

1. **Target.** Binary close-to-close direction (`close[i+h-1] > close[i-1]`) is dominated by noise.
   Daily DSE moves are near-random; binary direction has little learnable structure.
2. **Normalization.** A single global `StandardScaler` is fit across all tickers and all timesteps.
   A 5000-taka stock and a 20-taka stock are pooled into one distribution, destroying comparability.
3. **Evaluation.** Training watches only BCE loss. There is no rank IC, hit-rate, or backtest, so
   regressions in real signal are invisible.

Two referenced articles (arXiv 2501.17366v1; DataCamp LSTM tutorial) both do *price regression*
(predict next close). Their headline "96% accuracy" is regression R² on a near-random-walk series —
misleadingly easy and not tradeable. We adopt their useful mechanics (EMA smoothing, LR-plateau
decay, windowed normalization, attention/CNN as future levers) but keep an honest, tradeable target.

## Goal

Predict, per ticker per horizon, the **cross-sectional rank of forward return**, then rank all
tickers each date and select a top-N basket to buy. Success = positive rank IC and a backtest equity
curve that beats an equal-weight buy-hold proxy.

## Scope

In scope (v1, "Approach A", structured to accept "Approach B" later):
- New regression target (rank-return), 6 horizons.
- Two-stage normalization with per-date cross-sectional z-scoring.
- 3 new features using `open`; EMA feature smoothing.
- Attention-pooled LSTM with a swappable front-end slot.
- Per-epoch signal metrics (rank IC, top-N hit rate) + a standalone backtest harness.

Out of scope (future phases):
- CNN-LSTM front end (Approach B) — architecture leaves a slot for it; not wired in v1.
- Full learning-to-rank cross-sectional model (Approach C).
- Walk-forward / rolling-window validation — v1 keeps a single chronological split.

## Design

### 1. Target & labels

For each ticker, date index `i`, horizon `h`:

```
fwd_ret[i, h] = close[i + h - 1] / close[i - 1] - 1
```

Horizons (Fibonacci): `HORIZONS = [1, 2, 3, 5, 8, 13]` → 6 output heads. `max_horizon = 13`.
Minimum usable history per ticker = `SEQ_LEN + max_horizon = 60 + 13 = 73` rows.

The training label is **not** raw `fwd_ret`. On each date, across all tickers present that date,
rank `fwd_ret[*, h]` and convert to a cross-sectional z-score (mean 0, std 1 across the
cross-section). This removes market-wide up/down days and isolates *relative* outperformance — the
tradeable signal that drives top-N selection.

- **Loss:** `HuberLoss` on rank-return, summed equally across the 6 heads (robust to fat-tailed
  DSE returns).
- **Inference:** model outputs predicted rank-return per horizon → sort descending → top-N basket.

Note: 1d/2d horizons are the noisiest and expected to show low IC; heads are independent so weak
horizons do not degrade the others. Basket selection weights toward whichever horizon backtests best.

### 2. Features & normalization

Feature columns (existing 10 + 3 new):

```
existing: rsi_14, macd_diff, bb_pband, atr_norm, adx_14,
          return_1d, return_5d, return_20d, volume_zscore, obv
new:      gap_open   = open / prev_close - 1
          body       = (close - open) / open
          mid_return = mid.pct_change(1)        # mid = (high + low) / 2
```

This requires adding `open` to the SQL `SELECT` and DataFrame in `build_sequences`, and computing
the 3 new columns in `compute_price_features`.

**Two-stage normalization:**

1. *Per-ticker temporal* — TA indicators are already ratios/oscillators (RSI, returns, pband), so no
   raw price is fed. Kept as-is.
2. *Per-date cross-sectional z-score* — at each timestamp, z-score every feature across all tickers
   present. This is the core fix: it makes a 20-taka and a 5000-taka stock comparable and aligns the
   inputs with the rank-return target.

**EMA smoothing** — apply a causal EMA (span ≈ 3) per ticker to feature columns before windowing, to
cut DSE daily noise. Causal → no leakage.

The global `StandardScaler` fit-on-train is removed. Cross-sectional normalization replaces it;
normalization stats are recomputed per-date at inference rather than stored in the checkpoint.

**Thin-date handling:** dates with fewer than a minimum number of tickers present are dropped before
cross-sectional normalization (accepted — affects only sparse early history).

### 3. Model architecture

Evolve `LSTMPredictor` in place (old checkpoints are incompatible regardless, since the target
changed). Interface (`forward → (batch, n_horizons)`, `predict_*`, `save`, `load`) is preserved.

```
Input (batch, 60, n_feat=13)
   │
   ├─ front_end: nn.Module   (default nn.Identity; B-slot for a future Conv1d stack)
   ▼
LSTM(hidden=64, num_layers=2, dropout=0.4)   → returns full sequence (batch, 60, 64)
   │
   ▼
Attention pool: learned query over 60 timesteps → (batch, 64)
   │
   ▼
Linear head → (batch, 6)   regression: rank-return for [1,2,3,5,8,13]d
```

- **Attention pooling** replaces the last-hidden readout, which discarded 59 of 60 steps. A small
  linear + softmax weights the informative timesteps.
- **B-readiness:** `front_end` is a module slot defaulting to `nn.Identity()`. A Conv1d block swaps
  in later with zero change to the LSTM, attention, heads, or training loop.
- `N_HORIZONS` constant changes 3 → 6. Heads become regression outputs (no sigmoid).
- `save`/`load` keep their signatures; scaler field becomes unused/None.

### 4. Training & evaluation

- **Split:** chronological 80/20, no shuffle (avoids leakage). Walk-forward deferred.
- **Loss:** summed Huber across 6 heads.
- **Optimizer:** Adam, lr 3e-4, weight_decay 1e-4 (unchanged).
- **Scheduler:** `ReduceLROnPlateau` on val loss, patience 3, factor 0.5. Early-stop patience 5.
- **Per-epoch metrics (new):**
  - *Rank IC* per horizon — Spearman corr(pred, actual rank-return). Primary signal metric; > ~0.03
    indicates real signal.
  - *Top-N hit rate* — of predicted top-20, fraction beating the cross-sectional median realized
    return.
  - Val Huber loss (drives early-stop and scheduler).
- **`--dump`** updated for the new target/features; otherwise unchanged.

### 5. Backtest harness (new)

A standalone module run post-training against the validation period:

- For each date: rank tickers by predicted rank-return, go long the top-20, equal weight, hold for
  the horizon.
- Outputs: cumulative return vs an equal-weight-all-tickers buy-hold proxy, Sharpe ratio, max
  drawdown, and the IC time series.
- This is the real acceptance gate — not validation loss.

## Files Affected

- `ml/models/lstm_predictor.py` — attention pooling, front-end slot, 6 regression heads.
- `ml/features/price_features.py` — 3 new `open`-based features.
- `ml/train/train_lstm.py` — `open` in SQL/DataFrame, rank-return labels, cross-sectional norm, EMA,
  6 horizons, scheduler, IC/hit-rate logging, updated `--dump`.
- `ml/eval/backtest.py` (new) — backtest harness + metrics.
- `ml/features/` or `ml/train/` helper — cross-sectional normalization + rank-return utilities
  (extracted so train, dump, and inference share one implementation).

## Acceptance Criteria

1. Training logs rank IC and top-N hit rate per horizon each epoch.
2. At least one horizon (expected 8d or 13d) shows mean val rank IC > 0.03.
3. Backtest top-20 basket beats the equal-weight buy-hold proxy on cumulative return over the
   validation period, with reported Sharpe and max drawdown.
4. No raw absolute price is fed to the model; all inputs pass two-stage normalization.
5. `LSTMPredictor` public interface unchanged; a Conv1d front end can be added without touching the
   training loop.

## Risks & Open Questions

- **Low absolute IC.** Even correct setups yield small IC on near-efficient markets; the backtest,
  not IC alone, is the gate.
- **Cross-sectional norm at inference** must use only tickers available at prediction time; live
  thin-coverage dates may produce noisy z-scores.
- **DSE return fat tails / circuit breakers** — Huber mitigates, but extreme-move days may still
  distort labels; may need winsorization (deferred unless backtest shows distortion).
