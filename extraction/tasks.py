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
    from extraction.jobs import job_run
    from ml.valuation.dcf import DCFCalculator
    from ml.scoring.health_score import compute_health_score

    async with job_run("nightly_ml") as ctx:
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
            # New model is cross-sectional (ranks all tickers together), so we
            # delegate to the batch inference entrypoint instead of per-ticker.
            from ml.inference.predict_prices import main as run_lstm_inference

            await run_lstm_inference()
            lstm_ok = 1
            logger.info("run_ml_inference: LSTM inference done")
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
                    SELECT eps FROM fundamentals
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
                eps_vals = [float(r["eps"]) for r in eps_rows if r["eps"] is not None]
                if len(eps_vals) < 2:
                    continue
                if eps_vals[-1] <= 0 or eps_vals[0] <= 0:
                    continue

                growth = (eps_vals[0] / eps_vals[-1]) ** (1 / (len(eps_vals) - 1)) - 1
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
        ctx["records_inserted"] = len(fund_scores) + lstm_ok + dcf_ok
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
    from extraction.jobs import job_run
    from extraction.observability import fire_alert
    from ml.monitoring.accuracy_report import check_accuracy_thresholds, populate_outcomes

    async with job_run("quarterly_retrain") as ctx:
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

        # LSTM retrain — delegates to the cross-sectional training entrypoint,
        # which builds sequences, trains (Huber regression on rank-return), and
        # saves to models/v1/lstm_v0.pt. We then archive a copy into the
        # versioned dir for that run.
        try:
            from ml.train.train_lstm import MODEL_PATH as LSTM_MODEL_PATH
            from ml.train.train_lstm import main as train_lstm_main

            await train_lstm_main()
            if LSTM_MODEL_PATH.exists():
                versioned_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy(LSTM_MODEL_PATH, versioned_dir / "lstm_v0.pt")
            logger.info("retrain_ml_models: LSTM retrained -> %s", LSTM_MODEL_PATH)
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
        ctx["records_inserted"] = n_outcomes
        return {"outcomes_evaluated": n_outcomes, "version": version, "errors": errors}


@celery_app.task(name="extraction.tasks.run_ml_inference", bind=True, max_retries=0)
def run_ml_inference(self) -> dict:
    """Nightly ML inference: fundamental scoring, LSTM price direction, DCF valuation.

    max_retries=0: write_scores (fundamental scoring) still uses plain INSERT, so
    automatic retry after partial completion would produce duplicate rows in
    stock_scores. The LSTM path now upserts (ON CONFLICT DO UPDATE) and is
    retry-safe; raise max_retries once write_scores also upserts.
    """
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
