# Kronos as Parallel Price-Forecast Model (`kronos_v0`) — Design

**Date:** 2026-06-01
**Status:** Design — pending implementation plan
**Author:** brainstormed with Claude Code

## Summary

Integrate [Kronos](https://github.com/shiyu-coder/Kronos) — a decoder-only foundation
model for financial K-line (candlestick) sequences — into the DSE platform's prediction
layer as a **parallel `model_version`** alongside the existing LSTM direction predictor.

Kronos tokenizes multi-dimensional OHLCV into hierarchical discrete tokens, then an
autoregressive transformer generates a **probabilistic OHLCV path** forecast. This is a
capability upgrade over the current LSTM, which predicts only binary direction
(`P(up)` for 5d/10d/20d). Running both in parallel gives a zero-cost A/B comparison
before any decision to replace the LSTM.

## Decisions (locked during brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Role | New parallel `model_version='kronos_v0'` | Lowest risk; clean A/B vs `lstm_v0`; LSTM stays |
| Compute / host | CPU inside existing extraction/scheduler worker | No new infra, no GPU bill; nightly batch is offline-OK |
| Training | Zero-shot pretrained (NeoQuasar checkpoints) | Fastest v0 baseline; fine-tune later only if A/B justifies |
| Output storage | Same `ml_predictions` fields **+** new rich-path table | Derived fields preserve A/B; path/bands are Kronos's differentiator and feed future charts |
| Model size | Kronos-small (24.7M, 512 ctx) | CPU sweet spot; `mini` (4M) fallback, `base` (102M) only with future GPU |

## Current State (what exists today)

- `ml/models/lstm_predictor.py` — 2-layer LSTM, 10 hand-crafted TA features
  (RSI/MACD/BB/ATR/ADX/returns/volume/OBV), 60-day window → binary `P(up)` for
  5d/10d/20d. Pooled across tickers (single shared model).
- `ml/inference/predict_prices.py` — `predict_ticker()` writes 3 rows/ticker to
  `ml_predictions`. `target_price` is a toy heuristic: `last_close * (1 + (p-0.5)*0.1)`.
- `ml/features/feature_store.py` — `build_price_feature_matrix()` fetches
  `close/high/low/volume` (**no `open`**) and computes TA features.
- `ml/train/train_lstm.py` — pooled training, chronological split, `StandardScaler`.
- `extraction/tasks.py` — `_run_ml_inference_async()` runs the nightly pipeline
  (fundamental scoring → LSTM → DCF/health). Enqueued by `job_nightly_ml` (22:00 BD)
  to the Celery `ml` queue. `run_ml_inference` is `max_retries=0`.
- `ml_predictions` table (migration 018): `ticker, predicted_at, horizon_days,
  predicted_direction, confidence, target_price, model_version` (default `'lstm_v0'`).
  Unique constraint (migration 024) keys on `(ticker, horizon_days, model_version,
  prediction_date)` — so a second `model_version` coexists cleanly.
- `prediction_outcomes` + `ml/monitoring/accuracy_report.py` evaluate accuracy
  **per `model_version`** — A/B comparison already supported, no new eval code.
- `torch>=2.3` already a dependency (`pyproject.toml`).

## Kronos — key facts

- Decoder-only foundation model; tokenizer quantizes OHLCV → discrete tokens, then
  autoregressive transformer. Probabilistic forecast (temperature / nucleus sampling).
- Input: pandas DataFrame with `['open','high','low','close']` (volume/amount optional).
- Output: DataFrame of predicted OHLCV for specified future timestamps. `predict()` and
  `predict_batch()`.
- Sizes: mini 4.1M (2048 ctx) · small 24.7M (512 ctx) · base 102.3M (512 ctx).
- Checkpoints on HF Hub (`NeoQuasar/Kronos-*`, `NeoQuasar/Kronos-Tokenizer-*`),
  `from_pretrained()`. MIT license.
- Not published on PyPI — model code lives in the GitHub repo (`KronosPredictor` class).

## Placement

Kronos slots into the **existing nightly ML pipeline** as a sibling of the LSTM. No new
service, no new scheduler job.

```
job_nightly_ml (22:00 BD)  →  Celery ml queue  →  _run_ml_inference_async()  [extraction/tasks.py]
    step 1 : fundamental scoring (XGBoost)
    step 2 : LSTM direction        → ml_predictions (model_version='lstm_v0')
 ►  step 2b: KRONOS forecast        → ml_predictions (model_version='kronos_v0')   ← NEW
 ►                                  + ml_forecast_paths (full path + bands)        ← NEW
    step 3 : DCF + health score
```

## Components (new code, mirrors the LSTM trio)

- `ml/vendor/kronos/` — vendored Kronos model code (tokenizer, model, `KronosPredictor`;
  ~3 files, MIT). Vendored rather than git submodule to avoid submodule fragility.
- `ml/models/kronos_wrapper.py` — loads tokenizer + model from HF cache, wraps
  `KronosPredictor`, exposes `forecast(ohlcv_df, horizon, n_samples) -> list[path]`.
- `ml/inference/predict_kronos.py` — `predict_ticker_kronos(pool, predictor, ticker)`:
  fetch OHLCV → forecast → derive fields → write both tables. Mirrors `predict_prices.py`.
- `ml/features/feature_store.py` — add `build_ohlcv_matrix(pool, ticker, lookback)`:
  fetches **raw `open/high/low/close/volume`** (Kronos needs `open`, currently not fetched),
  filters to bars with `open IS NOT NULL` and `quality_flag <> 'bad'`.
- `extraction/tasks.py` — add step 2b to `_run_ml_inference_async()`, guarded by model
  presence (skip + log if checkpoint absent), per-ticker try/except like the LSTM step.

## Data flow + derive logic

For a ticker, fetch last 60 OHLCV bars, forecast 20 steps with `S` sampled paths
(`n_samples ≈ 30`, temperature/nucleus). From the sampled paths:

For each horizon `h ∈ {5, 10, 20}`:
- `P(up)` = fraction of sampled paths where `close_h > last_close`
- `predicted_direction` = `up` if `P ≥ 0.5` else `down`
- `confidence` = `max(P, 1 - P)`  → **same schema as LSTM → apples-to-apples A/B**
- `target_price` = median `close_h` across paths (a real forecast, vs the LSTM toy heuristic)

Rich output (the reason Kronos was chosen):
- Per ticker per run, the p10/p50/p90 close band across the 20-day path → `ml_forecast_paths`.
  Powers the price-path charts planned for the phase-C stock pages.

## Schema

`ml_predictions` needs **no change** — `kronos_v0` coexists with `lstm_v0` under the
existing unique constraint. One new migration:

```sql
-- db/migrations/030_ml_forecast_paths.sql
CREATE TABLE IF NOT EXISTS ml_forecast_paths (
    id              BIGSERIAL    PRIMARY KEY,
    ticker          TEXT         NOT NULL REFERENCES companies(ticker),
    predicted_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    prediction_date DATE         NOT NULL,
    model_version   TEXT         NOT NULL DEFAULT 'kronos_v0',
    horizon_days    SMALLINT     NOT NULL,        -- max horizon of this path (e.g. 20)
    path            JSONB        NOT NULL,         -- [{day, p10, p50, p90, vol}, ...]
    UNIQUE (ticker, model_version, prediction_date)
);

CREATE INDEX IF NOT EXISTS idx_ml_forecast_paths_ticker
    ON ml_forecast_paths (ticker, prediction_date DESC);
```

Path stored as JSONB (one document per ticker/run) rather than one row per future day —
simpler reads for charting, fewer rows, no need to query individual days relationally.

## Dependencies + weights persistence

- Add `huggingface_hub` to `pyproject.toml`; add any Kronos `requirements.txt` extras
  not already present (likely `einops`, `safetensors`). `torch>=2.3` already present.
- Weights pulled once via `from_pretrained("NeoQuasar/Kronos-small")` (+ tokenizer),
  cached to a **persistent Docker volume** via `HF_HOME`, pinned to the same volume as
  `models/v1/`. (Memory flags model-file volume-mount risk — the plan addresses this
  explicitly.) Worker runs fully offline after first pull.

## Compute sizing

~400 active tickers × (`S` sampled paths × 20 autoregressive steps). Use `predict_batch`.
Estimated single-digit minutes on CPU within the 22:00 offline window — acceptable. The
implementation plan benchmarks a 20-ticker slice before enabling the full run; falls back
to `Kronos-mini` if runtime is unacceptable.

## Error handling

- Per-ticker `try/except` — skip on failure, log, continue batch (mirrors LSTM step).
- Writes use `ON CONFLICT (ticker, model_version, prediction_date) DO UPDATE`, so
  `run_ml_inference` stays `max_retries=0`-safe without the duplicate-row bug noted in
  project memory (`project_ml_pipeline_bugs`).
- Missing/short OHLCV, or missing `open` (live-only rows), → skip ticker.
- Step 2b guarded by checkpoint presence; absent model logs a warning and skips.

## A/B evaluation (free)

`prediction_outcomes` + `accuracy_report` already key on `model_version`. Once `kronos_v0`
rows land, the quarterly accuracy check compares Kronos vs LSTM directional accuracy with
zero new evaluation code. This is the go/no-go signal for a later "replace LSTM" decision.

## Testing

- **Unit (offline):** derive logic (sampled paths → direction/confidence/target/bands)
  with a mocked predictor; schema-write tests against the unique constraint. Matches the
  existing pickle-fixture / offline-unit pattern.
- **Smoke (opt-in, marked):** one real `from_pretrained` + 1-ticker forecast to generate
  a fixture.

## Out of scope (YAGNI)

- Fine-tuning on DSE history (zero-shot v0 per decision; revisit if A/B justifies).
- GPU / dedicated inference service / ensemble blending with LSTM+fundamentals.
- LSTM removal.
- Frontend chart work (the `ml_forecast_paths` table is *ready* for it; the UI is a
  separate spec).

## Open questions for the plan

- Exact Kronos `requirements.txt` extras to add (confirm against repo).
- `n_samples` and temperature/top-p defaults (tune on the benchmark slice).
- Whether to also write volume bands to `ml_forecast_paths` or close-only for v0.
