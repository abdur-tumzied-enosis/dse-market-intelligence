# ML Celery Tasks — Design Spec

**Date:** 2026-05-25
**Goal:** Move ML inference and retrain logic out of the APScheduler process into Celery `ml` queue tasks so the scheduler event loop is never blocked by CPU-bound PyTorch/XGBoost work.

---

## Problem

`job_nightly_ml` and `job_quarterly` in `extraction/scheduler.py` run all ML logic inline — PyTorch LSTM inference (406 tickers), XGBoost scoring, DCF valuation, and full model retraining. These are CPU-bound blocking calls inside an async event loop, which freezes the scheduler for minutes during nightly inference and potentially 5–15 minutes during quarterly retrain.

The `dse_worker` Celery container already has an `ml` queue and stub tasks (`run_ml_inference`, `retrain_ml_models`) in `extraction/tasks.py`. Those stubs are unimplemented TODOs.

---

## Approach: `asyncio.run()` inside Celery tasks

Celery tasks are synchronous. The ML codebase uses async DB access (asyncpg). Bridge: wrap async ML logic in `asyncio.run()` inside each task. Standard pattern, no new dependencies, one new event loop per task — acceptable cost for tasks that fire once per day.

---

## Changes

### `extraction/tasks.py`

Implement two tasks. Logic is extracted verbatim from `scheduler.py` — no ML code changes.

**`run_ml_inference`** — mirrors `job_nightly_ml`:
1. Load `models/v1/fundamental_scorer.pkl` → `score_all_tickers()` → `write_scores()`
2. Load `models/v1/lstm_v0.pt` → `predict_ticker()` for every active ticker
3. DCF valuation per ticker → update `stock_scores.valuation_score` + `health_score`

**`retrain_ml_models`** — mirrors `job_quarterly`:
1. `populate_outcomes()` — catch up unevaluated predictions
2. `check_accuracy_thresholds()` — fire alert if accuracy degraded
3. Retrain XGBoost (min 30 samples required)
4. Retrain LSTM (min 200 sequences, max 30 epochs, early stopping patience=5)
5. Save versioned copy → `models/YYYYMMDD/`, overwrite `models/v1/`
6. Fire INFO summary alert

Both tasks use `bind=True` (already in stubs) and retain existing `max_retries` values (`3` for inference, `1` for retrain).

Async DB work is driven via `asyncio.run()`:
```python
def run_ml_inference(self) -> dict:
    return asyncio.run(_run_ml_inference_async())
```

### `extraction/scheduler.py`

Strip ML logic from both jobs. Replace with `.delay()` enqueue calls.

```python
async def job_nightly_ml() -> None:
    from extraction.tasks import run_ml_inference
    run_ml_inference.delay()
    logger.info("job_nightly_ml: enqueued run_ml_inference to ml queue")

async def job_quarterly() -> None:
    from extraction.tasks import retrain_ml_models
    retrain_ml_models.delay()
    logger.info("job_quarterly: enqueued retrain_ml_models to ml queue")
```

The scheduler becomes a thin dispatcher. All ML logic lives in tasks.py.

### `docker-compose.yml`

Add comment above the worker service:

```yaml
# NOTE: models/ directory is written by the worker container (models/v1/, models/YYYYMMDD/).
# In dev, the ./:/app bind mount makes this transparent (files land on host disk).
# TODO: In production, add a named Docker volume for models/ so files persist
# across container rebuilds and are shared if multiple workers run.
```

No functional changes to docker-compose.

---

## What Does NOT Change

- ML model code (`ml/` directory) — untouched
- DB schema — no changes
- Celery app config (`extraction/celery_app.py`) — routes already correct
- Worker command in docker-compose — already listens on `ml` queue
- Retry/timeout config — existing values kept

---

## Out of Scope

- Models Docker volume (deferred — see NOTE above)
- Other stub tasks (`scrape_all_fundamentals`, `process_new_articles`, `recalculate_beta_all_stocks`)
- Per-task progress reporting or Celery result storage
