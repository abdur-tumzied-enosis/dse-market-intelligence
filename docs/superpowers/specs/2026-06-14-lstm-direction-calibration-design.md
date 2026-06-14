# LSTM Per-Stock Direction + Confidence: Honest Validation & Calibration

**Date:** 2026-06-14
**Status:** Approved (design)
**Track:** Approach A — validate the existing rank-return LSTM, calibrate its output into an honest per-stock direction + confidence, then make `predict_prices.py` ship that honest number. Retrain (B) / reframe (C) are deferred until the harness shows whether the target is reachable.

## Problem

The product surfaces a per-stock **direction (up/down) + confidence (%)** per horizon on the stock detail page (`ml_predictions`). Today that number is dishonest:

- `predict_prices.py:94` — `direction = "up" if scores[ti] >= 0 else "down"`. The LSTM predicts **cross-sectional rank-return** (z-scored "beats peer average"), not absolute price direction. A "up" call only means *ranked above peers* — in a falling market a "up" stock can drop.
- `predict_prices.py:92-95` — `confidence = cross-sectional percentile rank` of the prediction. The top-ranked stock always gets ~1.0. This is a position in the cross-section, **not** a probability of being correct. Never validated, never calibrated.
- `accuracy_report.py:65-66` — grades `correct` by comparing the predicted (relative) direction against **absolute** realized up/down. It is silently grading a relative call as if it were absolute.

Prior context (memory):
- DSE is **reversal-dominated**: a momentum scorer was validated point-in-time, found rank-IC *negative*, and reverted. The LSTM consumes mostly momentum/trend features — same trap risk.
- The LSTM end-to-end gate (`ml/eval/backtest.py`) was **never run on live data**.
- The 2022-07 → 2024-01 **BSEC floor-price regime** froze many DSE stocks at regulatory floors → any return-trained signal in that window is untrustworthy.
- The old binary up/down model was ~50% (near-random) and lost money. Absolute daily direction on DSE is near-efficient.

So "make predictions better" must start by measuring whether the current model's output carries any honest directional signal, then making the shipped number truthful — not by tuning architecture on an unvalidated, possibly-backwards model.

## Goals

1. Measure, point-in-time and out-of-sample, whether the LSTM output predicts: (a) relative rank (rank-IC), (b) absolute direction (hit-rate > 50%), and (c) is calibrated (does "70%" mean ~70%?).
2. Produce per-horizon calibrators mapping raw model score → P(absolute up).
3. Make `predict_prices.py` write an honest direction + calibrated confidence; show low conviction as low conviction (or suppress) when a horizon is near-random.

## Non-Goals (deferred)

- Retraining a dedicated direction/classification model (Approach B).
- Reframing the UI copy to relative "outperform peers" language (Approach C).
- Architecture changes, ranking losses, new features, walk-forward training, ensembling.
- Frontend changes (the UI already renders direction + confidence; only the values change).

These are revisited only after the D1 harness numbers are known.

## Components

### D1 — Validation harness: `ml/eval/lstm_validation.py`

Mirror the structure of `ml/eval/momentum_validation.py` (point-in-time, survivorship-aware, regime/bucket splits, report dict + printed summary).

- **Data/model:** load trained `models/v1/lstm_v0.pt`; rebuild full historical windows via a **shared** `build_sequences` (see Refactor below); run `model.predict` over all windows → per-(time, ticker, horizon) raw score, alongside `meta`'s raw `fwd_ret_h`.
- **Out-of-sample only:** reproduce training's chronological 80/20 split by sample `time`, evaluate the **val tail only**, and apply a **purge gap = max(HORIZONS)** trading dates at the train/val boundary (training itself has no purge; the harness adds it so val windows don't overlap train labels).
- **Per horizon, three framings:**
  1. **Rank-IC** — `rank_ic(pred, raw_fwd_ret)` per date, summarized like `momentum_validation._ic_summary` (mean, std, IR, t-stat, %positive). Does the *relative* signal exist?
  2. **Directional hit-rate** — treat `pred >= 0` as an "up" call; `actual_up = raw_fwd_ret > 0`; hit-rate = mean(call == actual), with base rate (market up-fraction) for comparison. Is the *absolute* call better than 50% / better than base rate?
  3. **Calibration** — bin samples by the to-be-shipped confidence proxy, compute realized accuracy per bin + Brier score + reliability table. Measured pre-calibration here to quantify the gap D2 must close.
- **Regime split:** report `full` vs `ex_floor` (samples whose forward window does **not** overlap the floor regime). Floor window is a module constant `FLOOR_REGIME = (date(2022,7,28), date(2024,1,22))`, configurable.
- **Survivorship:** `build_sequences` includes any ticker with sufficient history (not `is_active`-filtered) → acceptably survivorship-aware; note it in the report header.
- **Entry point:** `python -m ml.eval.lstm_validation`; returns a report dict and prints a summary. Requires live TimescaleDB + a trained model; if the model is missing, raise with the instruction to run `ml.train.train_lstm` first.

### D2 — Direction calibrator: `ml/eval/calibrate_direction.py`

- Per-horizon **isotonic regression** mapping raw model score → P(absolute up = `raw_fwd_ret > 0`). Use `sklearn.isotonic.IsotonicRegression` directly (per `project_sklearn_calibration_gotcha`: `CalibratedClassifierCV(cv="prefit")` is removed in sklearn 1.9.0).
- Fit on an OOS calibration slice with a **walk-forward / chronological** split so fit and evaluation never share dates; report Brier before/after and a reliability table per horizon.
- Persist to `models/v1/lstm_direction_calibrators.pkl` — a dict `{horizon_days: fitted_isotonic}` plus metadata (fit date range, n samples, per-horizon Brier and base rate). A horizon whose OOS hit-rate is statistically indistinguishable from its base rate is flagged `low_signal=True` in the metadata so D3 can suppress it.
- **Entry point:** `python -m ml.eval.calibrate_direction`.

### D3 — Honest inference: edit `ml/inference/predict_prices.py`

- Load `lstm_direction_calibrators.pkl` (if present).
- Per ticker/horizon: `p_up = calib[h](raw_score)`; `direction = "up" if p_up >= 0.5 else "down"`; `confidence = p_up if up else 1 - p_up` (probability of the stated side).
- If no calibrator file, or the horizon is flagged `low_signal`: write honest low conviction — `confidence ≈ 0.5` (neutral) — or skip writing that horizon. Decision per horizon driven by D1/D2 metadata; default to writing neutral 0.5 rather than a fake high number.
- Remove the percentile-rank-as-confidence path. Keep the cross-sectional z-score + windowing untouched (train/serve parity).

### D4 — Monitoring alignment: `ml/monitoring/accuracy_report.py`

- Once D3 makes `predicted_direction` an absolute up/down call, the existing `populate_outcomes` comparison (`actual_direction = up if h_price > p_price`) becomes semantically correct. No code change expected. Re-read after D1; add a guard/comment only if the harness reveals a concrete mismatch (e.g. needs to compare against a peer-relative baseline).

### Refactor — shared `build_sequences`

`build_sequences` currently lives in `ml/train/train_lstm.py`. D1 needs the identical pipeline (per-ticker `clean_and_smooth` → forward returns → cross-sectional z-score → `build_windows`) to guarantee train/eval parity. Extract it into a shared module (e.g. `ml/features/sequence_builder.py` or `ml/train/dataset.py`) and import it from both `train_lstm.py` and `lstm_validation.py`. Keep behavior identical; this is the train/serve-parity invariant (`project_lstm_ranking_redesign`).

## Data Flow

```
stock_prices ──> build_sequences (shared) ──> X, y, meta(raw fwd_ret)
                                                  │
                          load lstm_v0.pt ──> model.predict(X) ──> raw scores
                                                  │
                    OOS tail + purge gap ─────────┤
                                                  ▼
              D1 lstm_validation: rank-IC | hit-rate | calibration  (full vs ex_floor)
                                                  │  (raw score, actual_up) pairs
                                                  ▼
              D2 calibrate_direction: per-horizon isotonic ──> lstm_direction_calibrators.pkl
                                                  │
                                                  ▼
              D3 predict_prices: p_up = calib(score) ──> ml_predictions(direction, confidence=P)
                                                  │
                                                  ▼
              D4 accuracy_report: grades absolute vs absolute (now aligned)
```

## Testing

Unit tests stay fully offline (synthetic arrays / tiny DataFrames; no DB):

- `rank_ic` / hit-rate / calibration-error + reliability-bin metric: known-input expected-output (perfect, inverse, random signals).
- Calibration mapping: isotonic is monotonic; perfectly-separable synthetic data → near-0 Brier; random data → Brier ≈ base-rate variance.
- Harness slicing: synthetic windowed dataset → purge gap drops the right boundary dates; regime split partitions correctly; OOS tail excludes train rows.
- `predict_prices`: with a stub calibrator, written `confidence` equals the calibrated P (not the percentile rank); `low_signal` horizon writes neutral/skip.

Live run (manual, against DB + trained model): `python -m ml.train.train_lstm` → `python -m ml.eval.lstm_validation` → `python -m ml.eval.calibrate_direction` → `python -m ml.inference.predict_prices`.

## Acceptance

- D1 prints per-horizon rank-IC, directional hit-rate vs base rate, and pre-calibration Brier/reliability, split full vs ex_floor — giving a clear read on whether any honest directional signal exists.
- D2 produces persisted per-horizon calibrators with reported Brier improvement and `low_signal` flags.
- D3 ships calibrated P as confidence and an honest direction; near-random horizons show ~0.5 conviction or are suppressed — no fabricated high-confidence numbers remain.
- All new unit tests pass offline; `make check` clean.

## Risks / Open Questions

- **The honest answer may be "no signal."** If hit-rate ≈ base rate everywhere, D3 will correctly show low conviction. That is a successful outcome of A and the trigger to decide B vs C — not a failure to patch around.
- **Floor-regime dates are approximate.** Constant is configurable; refine if the ex_floor split looks mis-cut.
- **Sample overlap / t-stat inflation.** Overlapping forward windows make t-stats anti-conservative (same caveat as `momentum_validation`). Treat as directional.
- **No trained model present locally.** Harness requires running training first against a populated DB; document in run steps.
