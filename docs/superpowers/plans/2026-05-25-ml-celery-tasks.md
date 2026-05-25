# ML Celery Tasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move ML inference and retrain logic from the APScheduler process into Celery `ml` queue tasks so the scheduler event loop is never blocked by CPU-bound work.

**Architecture:** Two async inner functions (`_run_ml_inference_async`, `_retrain_ml_models_async`) hold all ML logic. Sync Celery task wrappers call them via `asyncio.run()`. Scheduler jobs become 3-line dispatchers that call `.delay()`. No ML code is changed.

**Tech Stack:** Celery 5, asyncio, PyTorch (CPU), XGBoost, asyncpg, APScheduler

---

## File Map

| File | Change |
|---|---|
| `extraction/tasks.py` | Add `asyncio` import; add 2 async inner functions; replace 2 stub task bodies |
| `extraction/scheduler.py` | Replace `job_nightly_ml` and `job_quarterly` bodies with `.delay()` calls |
| `docker-compose.yml` | Add NOTE comment above worker service |
| `tests/unit/test_tasks.py` | New file — tests for the two Celery tasks |
| `tests/unit/test_scheduler_ml.py` | New file — tests that scheduler dispatches to Celery |

---

## Task 1: Tests for `run_ml_inference` and `retrain_ml_models`

**Files:**
- Create: `tests/unit/test_tasks.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_tasks.py` with this content:

```python
"""Unit tests for ML Celery tasks in extraction/tasks.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from extraction import tasks


def test_run_ml_inference_delegates_to_async_inner():
    """Task calls _run_ml_inference_async and returns its result."""
    expected = {"fundamental_scored": 3, "lstm_predicted": 5, "dcf_valued": 2}
    mock = AsyncMock(return_value=expected)

    with patch.object(tasks, "_run_ml_inference_async", mock):
        result = tasks.run_ml_inference.apply().get()

    mock.assert_called_once()
    assert result == expected


def test_run_ml_inference_returns_dict_with_expected_keys():
    """Task result always has three count keys."""
    mock = AsyncMock(return_value={"fundamental_scored": 0, "lstm_predicted": 0, "dcf_valued": 0})

    with patch.object(tasks, "_run_ml_inference_async", mock):
        result = tasks.run_ml_inference.apply().get()

    assert "fundamental_scored" in result
    assert "lstm_predicted" in result
    assert "dcf_valued" in result


def test_retrain_ml_models_delegates_to_async_inner():
    """Task calls _retrain_ml_models_async and returns its result."""
    expected = {"outcomes_evaluated": 12, "version": "20260525", "errors": []}
    mock = AsyncMock(return_value=expected)

    with patch.object(tasks, "_retrain_ml_models_async", mock):
        result = tasks.retrain_ml_models.apply().get()

    mock.assert_called_once()
    assert result == expected


def test_retrain_ml_models_returns_dict_with_expected_keys():
    """Task result always has three keys."""
    mock = AsyncMock(return_value={"outcomes_evaluated": 0, "version": "20260525", "errors": []})

    with patch.object(tasks, "_retrain_ml_models_async", mock):
        result = tasks.retrain_ml_models.apply().get()

    assert "outcomes_evaluated" in result
    assert "version" in result
    assert "errors" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_tasks.py -v
```

Expected: 4 failures — `AttributeError: module 'extraction.tasks' has no attribute '_run_ml_inference_async'`

---

## Task 2: Implement `run_ml_inference` and `retrain_ml_models`

**Files:**
- Modify: `extraction/tasks.py`

- [ ] **Step 1: Replace `extraction/tasks.py` with full implementation**

```python
"""
Celery task definitions for heavy/slow jobs in DSE extraction pipeline.

Job types:
- scraper: fundamental scraping (18 min for 350 pages) — rate limited
- nlp: article sentiment scoring + embedding (Haiku) — per-article
- ml: ML model training + inference — GPU/heavy computation

Task routes allow Celery to dispatch jobs to specialized workers.
"""
from __future__ import annotations

import asyncio
import logging

from extraction.celery_app import celery_app

logger = logging.getLogger(__name__)


# ── Scraper Queue (I/O intensive) ──────────────────────────────────


@celery_app.task(name="extraction.tasks.scrape_all_fundamentals", bind=True, max_retries=3)
def scrape_all_fundamentals(self) -> dict:
    """Scrape fundamentals for all tickers (350 pages, ~18 min).

    Runs with 2.5s delay between requests (polite rate limiting).
    """
    logger.info("task: scrape_all_fundamentals: starting")
    # TODO: get tickers from db
    # TODO: for each ticker:
    #   - scrape fundamental page (bdshare or amarstock)
    #   - upsert to db
    #   - sleep(2.5) for rate limiting
    #   - on error: log and continue (don't break batch)
    logger.info("task: scrape_all_fundamentals: complete")
    return {"status": "complete", "task": "scrape_all_fundamentals"}


@celery_app.task(name="extraction.tasks.check_index_composition", bind=True, max_retries=3)
def check_index_composition(self) -> dict:
    """Check if DSE index composition changed (new/delisted companies)."""
    logger.info("task: check_index_composition: starting")
    # TODO: fetch current DSEX/DS30/DSES index membership
    # TODO: compare with previous snapshot
    # TODO: on change: alert + log + update companies table
    logger.info("task: check_index_composition: complete")
    return {"status": "complete", "task": "check_index_composition"}


# ── NLP Queue (LLM sentiment + embedding) ──────────────────────────


@celery_app.task(name="extraction.tasks.process_new_articles", bind=True, max_retries=3)
def process_new_articles(self) -> dict:
    """Score sentiment on new articles + generate embeddings.

    Uses Haiku for sentiment (cheap), pgvector for embeddings (semantic search).
    """
    logger.info("task: process_new_articles: starting")
    # TODO: get unprocessed articles from db
    # TODO: for each article:
    #   - call haiku sentiment API
    #   - generate embedding (Claude or sentence-transformers)
    #   - upsert sentiment + embedding to db
    logger.info("task: process_new_articles: complete")
    return {"status": "complete", "task": "process_new_articles"}


# ── ML Queue (heavy computation) ───────────────────────────────────


async def _run_ml_inference_async() -> dict:
    """Async body for run_ml_inference task."""
    from datetime import datetime, timezone
    from pathlib import Path

    from db.pool import get_pool
    from ml.valuation.dcf import DCFCalculator
    from ml.scoring.health_score import compute_health_score

    pool = await get_pool()
    fund_scores: dict[str, float] = {}

    # ── 1. Fundamental scoring (XGBoost) ──────────────────────────
    fund_path = Path("models/v1/fundamental_scorer.pkl")
    if fund_path.exists():
        from ml.models.fundamental_scorer import FundamentalScorer
        from ml.inference.score_fundamentals import score_all_tickers, write_scores

        scorer = FundamentalScorer()
        scorer.load(fund_path)
        scored_at = datetime.now(timezone.utc)
        fund_scores = await score_all_tickers(pool, scorer)
        await write_scores(pool, fund_scores, scored_at)
        logger.info("run_ml_inference: fundamental scoring done n=%d", len(fund_scores))
    else:
        logger.warning("run_ml_inference: fundamental_scorer.pkl not found — skipping")

    # ── 2. LSTM price direction ────────────────────────────────────
    lstm_path = Path("models/v1/lstm_v0.pt")
    lstm_ok = 0
    if lstm_path.exists():
        from ml.models.lstm_predictor import LSTMPredictor
        from ml.inference.predict_prices import predict_ticker

        model = LSTMPredictor.load(lstm_path)
        model.eval()
        tickers = await pool.fetch(
            "SELECT ticker FROM companies WHERE is_active = true"
        )
        for row in tickers:
            try:
                await predict_ticker(pool, model, row["ticker"])
                lstm_ok += 1
            except Exception as exc:
                logger.warning(
                    "run_ml_inference: lstm skip ticker=%s error=%s", row["ticker"], exc
                )
        logger.info("run_ml_inference: LSTM inference done n=%d", lstm_ok)
    else:
        logger.warning("run_ml_inference: lstm_v0.pt not found — skipping")

    # ── 3. DCF valuation + health score ───────────────────────────
    tickers = await pool.fetch(
        "SELECT ticker FROM companies WHERE is_active = true"
    )
    dcf_ok = 0
    for row in tickers:
        ticker = row["ticker"]
        try:
            fund_row = await pool.fetchrow(
                """
                SELECT eps, pe FROM fundamentals
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
                health = compute_health_score(
                    fundamental_score=fund_score,
                    valuation_score=valuation_score,
                )
                await pool.execute(
                    """
                    UPDATE stock_scores SET valuation_score = $1, health_score = $2
                    WHERE ticker = $3 AND scored_at = (
                        SELECT MAX(scored_at) FROM stock_scores WHERE ticker = $3
                    )
                    """,
                    valuation_score,
                    health,
                    ticker,
                )
                dcf_ok += 1
        except Exception as exc:
            logger.warning("run_ml_inference: dcf skip ticker=%s error=%s", ticker, exc)

    logger.info(
        "run_ml_inference: complete fundamental=%d lstm=%d dcf=%d",
        len(fund_scores), lstm_ok, dcf_ok,
    )
    return {
        "fundamental_scored": len(fund_scores),
        "lstm_predicted": lstm_ok,
        "dcf_valued": dcf_ok,
    }


async def _retrain_ml_models_async() -> dict:
    """Async body for retrain_ml_models task."""
    import shutil
    from datetime import datetime, timezone
    from pathlib import Path

    from db.pool import get_pool
    from extraction.observability import fire_alert
    from ml.monitoring.accuracy_report import check_accuracy_thresholds, populate_outcomes

    pool = await get_pool()

    # ── 1. Catch up unevaluated prediction outcomes ────────────────
    n_outcomes = await populate_outcomes(pool)
    logger.info("retrain_ml_models: outcomes populated: %d", n_outcomes)

    # ── 2. Accuracy check + alert ──────────────────────────────────
    check = await check_accuracy_thresholds(pool)
    if check["alert_level"]:
        logger.warning(
            "retrain_ml_models: accuracy degraded level=%s horizon=%s accuracy=%s",
            check["alert_level"], check["worst_horizon"], check["worst_accuracy"],
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

    # ── 3+4. Retrain models ────────────────────────────────────────
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
            shutil.copy(
                versioned_dir / "fundamental_scorer.pkl",
                current_dir / "fundamental_scorer.pkl",
            )
            logger.info("retrain_ml_models: XGBoost retrained on %d samples", len(X))
        else:
            logger.warning("retrain_ml_models: XGBoost skipped — only %d samples", len(X))
    except Exception as exc:
        logger.error("retrain_ml_models: XGBoost failed: %s", exc)
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
                TensorDataset(
                    torch.from_numpy(X_arr[:split]),
                    torch.from_numpy(y_arr[:split]),
                ),
                batch_size=64,
                shuffle=True,
            )
            val_dl = DataLoader(
                TensorDataset(
                    torch.from_numpy(X_arr[split:]),
                    torch.from_numpy(y_arr[split:]),
                ),
                batch_size=256,
                shuffle=False,
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
                val_losses = []
                with torch.no_grad():
                    for xb, yb in val_dl:
                        val_losses.append(criterion(model(xb), yb).item())
                val_loss = sum(val_losses) / len(val_losses)
                if val_loss < best_val_loss:
                    best_val_loss = val_loss
                    patience_count = 0
                    model.save(lstm_path)
                else:
                    patience_count += 1
                    if patience_count >= 5:
                        break

            shutil.copy(lstm_path, current_dir / "lstm_v0.pt")
            logger.info(
                "retrain_ml_models: LSTM retrained best_val_loss=%.4f", best_val_loss
            )
        else:
            logger.warning(
                "retrain_ml_models: LSTM skipped — only %d sequences", len(X_arr)
            )
    except Exception as exc:
        logger.error("retrain_ml_models: LSTM failed: %s", exc)
        errors.append(f"LSTM: {exc}")

    # ── 5. Summary alert ───────────────────────────────────────────
    await fire_alert(
        severity="INFO",
        stream_name="ml_predictions",
        message=f"Quarterly ML retrain complete (version={version})",
        details={"version": version, "errors": errors, "accuracy_check": check},
    )

    logger.info(
        "retrain_ml_models: complete version=%s errors=%s", version, errors
    )
    return {"outcomes_evaluated": n_outcomes, "version": version, "errors": errors}


@celery_app.task(name="extraction.tasks.run_ml_inference", bind=True, max_retries=3)
def run_ml_inference(self) -> dict:
    """Nightly ML inference: fundamental scoring, LSTM price direction, DCF valuation."""
    logger.info("task: run_ml_inference: starting")
    try:
        return asyncio.run(_run_ml_inference_async())
    except Exception as exc:
        logger.error("task: run_ml_inference: failed: %s", exc)
        raise self.retry(exc=exc)


@celery_app.task(name="extraction.tasks.retrain_ml_models", bind=True, max_retries=1)
def retrain_ml_models(self) -> dict:
    """Quarterly ML model retrain (full training + evaluation)."""
    logger.info("task: retrain_ml_models: starting")
    try:
        return asyncio.run(_retrain_ml_models_async())
    except Exception as exc:
        logger.error("task: retrain_ml_models: failed: %s", exc)
        raise self.retry(exc=exc)


@celery_app.task(name="extraction.tasks.recalculate_beta_all_stocks", bind=True, max_retries=3)
def recalculate_beta_all_stocks(self) -> dict:
    """Monthly beta calculation using 1-year rolling returns."""
    logger.info("task: recalculate_beta_all_stocks: starting")
    # TODO: for each ticker:
    #   - get 1-year returns (vs DSEX)
    #   - fit regression: stock_return ~ market_return
    #   - store beta coefficient in db
    logger.info("task: recalculate_beta_all_stocks: complete")
    return {"status": "complete", "task": "recalculate_beta_all_stocks"}
```

- [ ] **Step 2: Run the failing tests — they should now pass**

```
pytest tests/unit/test_tasks.py -v
```

Expected output:
```
tests/unit/test_tasks.py::test_run_ml_inference_delegates_to_async_inner PASSED
tests/unit/test_tasks.py::test_run_ml_inference_returns_dict_with_expected_keys PASSED
tests/unit/test_tasks.py::test_retrain_ml_models_delegates_to_async_inner PASSED
tests/unit/test_tasks.py::test_retrain_ml_models_returns_dict_with_expected_keys PASSED

4 passed
```

- [ ] **Step 3: Commit**

```bash
git add extraction/tasks.py tests/unit/test_tasks.py
git commit -m "feat(tasks): implement run_ml_inference and retrain_ml_models Celery tasks via asyncio.run"
```

---

## Task 3: Tests for scheduler dispatcher functions

**Files:**
- Create: `tests/unit/test_scheduler_ml.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_scheduler_ml.py`:

```python
"""Tests that job_nightly_ml and job_quarterly dispatch to Celery, not run ML inline."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from extraction import tasks
from extraction.scheduler import job_nightly_ml, job_quarterly


async def test_job_nightly_ml_enqueues_run_ml_inference():
    """job_nightly_ml must call run_ml_inference.delay() — never run ML inline."""
    mock_delay = MagicMock()
    with patch.object(tasks.run_ml_inference, "delay", mock_delay):
        await job_nightly_ml()
    mock_delay.assert_called_once_with()


async def test_job_quarterly_enqueues_retrain_ml_models():
    """job_quarterly must call retrain_ml_models.delay() — never run retrain inline."""
    mock_delay = MagicMock()
    with patch.object(tasks.retrain_ml_models, "delay", mock_delay):
        await job_quarterly()
    mock_delay.assert_called_once_with()
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_scheduler_ml.py -v
```

Expected: 2 failures — scheduler jobs currently run ML inline, don't call `.delay()`.

---

## Task 4: Update scheduler to dispatch via Celery

**Files:**
- Modify: `extraction/scheduler.py`

- [ ] **Step 1: Replace `job_nightly_ml` body**

Find the existing `job_nightly_ml` function in `extraction/scheduler.py` (lines ~429–553) and replace the entire function with:

```python
async def job_nightly_ml() -> None:
    """
    Nightly ML inference pipeline (~22:00 BD time, after EOD snapshot).

    Enqueues run_ml_inference to the Celery ml queue so CPU-bound PyTorch/XGBoost
    work runs in the worker process — not in this scheduler event loop.
    """
    from extraction.tasks import run_ml_inference
    run_ml_inference.delay()
    logger.info("job_nightly_ml: enqueued run_ml_inference to ml queue")
```

- [ ] **Step 2: Replace `job_quarterly` body**

Find the existing `job_quarterly` function in `extraction/scheduler.py` (lines ~274–418) and replace the entire function with:

```python
async def job_quarterly() -> None:
    """Quarterly ML retrain (Jan/Apr/Jul/Oct 1st, 03:00 BD time).

    Enqueues retrain_ml_models to the Celery ml queue. Worker handles full
    retrain (outcomes catchup → accuracy check → XGBoost + LSTM retrain → alert).
    """
    from extraction.tasks import retrain_ml_models
    retrain_ml_models.delay()
    logger.info("job_quarterly: enqueued retrain_ml_models to ml queue")
```

- [ ] **Step 3: Run tests to verify they pass**

```
pytest tests/unit/test_scheduler_ml.py -v
```

Expected output:
```
tests/unit/test_scheduler_ml.py::test_job_nightly_ml_enqueues_run_ml_inference PASSED
tests/unit/test_scheduler_ml.py::test_job_quarterly_enqueues_retrain_ml_models PASSED

2 passed
```

- [ ] **Step 4: Run full test suite to check for regressions**

```
pytest tests/unit/ -v
```

Expected: all existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add extraction/scheduler.py tests/unit/test_scheduler_ml.py
git commit -m "feat(scheduler): dispatch ML jobs to Celery worker instead of running inline"
```

---

## Task 5: Add models volume NOTE to docker-compose.yml

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add NOTE comment above the worker service**

Find this line in `docker-compose.yml`:

```yaml
  # ---------------------------------------------------------------------------
  # Celery worker — heavy async tasks (PDF extraction, bulk fundamentals, ML)
  # ---------------------------------------------------------------------------
  worker:
```

Replace it with:

```yaml
  # ---------------------------------------------------------------------------
  # Celery worker — heavy async tasks (PDF extraction, bulk fundamentals, ML)
  #
  # NOTE(models-volume): ML tasks write trained models to models/v1/ and
  # models/YYYYMMDD/ inside this container. In dev, the ./:/app bind mount
  # makes this transparent (files land on host disk). In production, add a
  # named Docker volume for models/ so files persist across container rebuilds
  # and are accessible if multiple worker replicas run.
  # TODO: add `- models_data:/app/models` volume + declare under `volumes:`.
  # ---------------------------------------------------------------------------
  worker:
```

- [ ] **Step 2: Commit**

```bash
git add docker-compose.yml
git commit -m "docs(docker): add TODO note for models/ named volume (production hardening)"
```

---

## Self-Review

**Spec coverage:**
- ✅ `run_ml_inference` task implements full `job_nightly_ml` ML logic
- ✅ `retrain_ml_models` task implements full `job_quarterly` ML logic
- ✅ Both use `asyncio.run()` — no new dependencies
- ✅ Scheduler jobs become 3-line dispatchers calling `.delay()`
- ✅ docker-compose NOTE added with TODO for future production hardening
- ✅ No ML code changes, no schema changes, no celery_app.py changes

**Placeholder scan:** No TBDs or incomplete steps. All code blocks are complete.

**Type consistency:** `_run_ml_inference_async` and `_retrain_ml_models_async` defined in Task 2, patched by exact same names in Task 1 tests. `run_ml_inference.delay` and `retrain_ml_models.delay` patched in Task 3, called in Task 4 scheduler bodies. ✅
