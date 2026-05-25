"""
APScheduler-based job scheduler for DSE data extraction pipeline.

Jobs are stored in PostgreSQL and survive service restarts.
Market hours: Sunday–Thursday, 10:00–14:30 BD time.

Terminology:
- light jobs (< 5s) → run in scheduler directly
- heavy jobs (> 5s or I/O bursts) → enqueue to Celery
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from sqlalchemy import create_engine

logger = logging.getLogger(__name__)
BD_TZ = pytz.timezone("Asia/Dhaka")


def get_scheduler(database_url: str) -> AsyncIOScheduler:
    """Create and configure APScheduler with PostgreSQL job store."""
    engine = create_engine(database_url, echo=False)

    scheduler = AsyncIOScheduler(
        jobstores={
            "default": SQLAlchemyJobStore(
                url=database_url.replace("sqlite:///", "postgresql://"),
                engine=engine,
            )
        },
        timezone=BD_TZ,
    )
    return scheduler


# ── Helpers ────────────────────────────────────────────────────────────


async def _upsert_macro_df(pool, df) -> int:
    """Insert/update rows from a macro AdapterResult DataFrame into macro_indicators."""
    import pandas as pd
    count = 0
    for _, row in df.iterrows():
        r = await pool.fetchrow(
            """
            INSERT INTO macro_indicators
                (indicator, value, unit, period, period_type, source, fetched_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (indicator, period, source) DO UPDATE
                SET value      = EXCLUDED.value,
                    fetched_at = EXCLUDED.fetched_at
            RETURNING id
            """,
            row["indicator"],
            row["value"],
            row.get("unit"),
            row["period"],
            row.get("period_type", "unknown"),
            row["source"],
            row["fetched_at"],
        )
        if r:
            count += 1
    return count


# ── Job Functions ──────────────────────────────────────────────────────


async def job_live_prices() -> None:
    """Fetch live prices every 15 min during market hours."""
    logger.info("job_live_prices: starting")
    # TODO: implement ingest_live_prices()
    try:
        from mgmt.cache import cache_delete_pattern
        await cache_delete_pattern("cache:pipeline_status:*")
        await cache_delete_pattern("cache:live_prices*")
    except Exception as exc:
        logger.warning("job_live_prices: cache invalidation failed", error=str(exc))
    logger.info("job_live_prices: complete")


async def job_eod_snapshot() -> None:
    """End-of-day snapshot and calculations."""
    logger.info("job_eod_snapshot: starting")
    # TODO: implement ingest_eod_snapshot()
    # TODO: update_52week_ranges()
    # TODO: update_circuit_breakers()
    # TODO: update_market_pe()
    # TODO: enqueue celery task: run_ml_inference
    logger.info("job_eod_snapshot: complete")


async def job_announcements() -> None:
    """Fetch DSE company announcements for all active tickers (daily after market close)."""
    from extraction.bulk_load.announcement_loader import bulk_load_announcements
    logger.info("job_announcements: starting")
    summary = await bulk_load_announcements()
    logger.info(
        "job_announcements: complete",
        ok=summary["ok"],
        failed=summary["failed"],
        inserted=summary["total_inserted"],
    )


async def job_news_scrape() -> None:
    """
    Fetch BD financial news via Google News RSS + extract DSE ticker mentions.

    Strategy:
      1. Fetch articles from GoogleNewsRSSAdapter
      2. Insert new articles (ON CONFLICT url DO NOTHING), RETURNING newly inserted ids
      3. Run Google NL API NER only on the returned (new) rows — zero API calls for duplicates
      4. UPDATE those rows with extracted tickers

    NL API free tier: 5,000 req/month. At 12h interval ~50 new articles/run
    = ~100 calls/day → ~3,000/month. Stays within free tier.
    If GOOGLE_CLOUD_API_KEY not set: articles saved with tickers=[].
    """
    from db.pool import get_pool
    from extraction.adapters.news.google_news_rss import GoogleNewsRSSAdapter
    from extraction.adapters.news.ticker_extractor import TickerExtractor, load_company_map
    from extraction.base import AdapterError
    from mgmt.config import get_settings

    cfg = get_settings()

    async with job_run("news_scrape", stream_name="news_en") as ctx:
        pool = await get_pool()

        # ── 1. Fetch articles ──────────────────────────────────────────────
        adapter = GoogleNewsRSSAdapter()
        try:
            result = await adapter.fetch()
        except AdapterError as exc:
            logger.error("news_scrape_adapter_failed", error=str(exc))
            raise

        df = result.data
        ctx["records_fetched"] = len(df)

        if df.empty:
            logger.info("news_scrape_no_articles")
            return

        # ── 2. Insert new articles, get back IDs of rows actually inserted ─
        # Dedup on content_hash (MD5 of headline+source), NOT url.
        # Google News proxy URLs regenerate for the same article → url dedup fails.
        newly_inserted: list[dict] = []
        for _, row in df.iterrows():
            rec = await pool.fetchrow(
                """
                INSERT INTO news
                    (source, url, headline, body, language, published_at, fetched_at,
                     tickers, ingestion_job, content_hash)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::text[], $9, $10)
                ON CONFLICT (content_hash) WHERE content_hash IS NOT NULL DO NOTHING
                RETURNING id, headline, body
                """,
                row["source"],
                row["url"],
                row["headline"],
                row.get("body") or None,
                row["language"],
                row["published_at"],
                row["fetched_at"],
                [],
                ctx["job_id"],
                row.get("content_hash"),
            )
            if rec:
                newly_inserted.append({"id": rec["id"], "headline": rec["headline"], "body": rec["body"] or ""})

        ctx["records_inserted"] = len(newly_inserted)
        logger.info("news_scrape_inserted", inserted=len(newly_inserted), skipped=len(df) - len(newly_inserted))

        # ── 3 + 4. NER: extract tickers for new articles only ─────────────
        if not newly_inserted or not cfg.google_cloud_api_key:
            if not cfg.google_cloud_api_key:
                logger.warning("news_scrape_no_ner_key", reason="GOOGLE_CLOUD_API_KEY not set — tickers empty")
            return

        company_map = await load_company_map(pool)
        extractor = TickerExtractor(cfg.google_cloud_api_key, company_map)

        for article in newly_inserted:
            text = f"{article['headline']} {article['body']}"
            result = await extractor.extract(text)
            if result.tickers or result.context_orgs or result.sentiment_score is not None:
                await pool.execute(
                    """
                    UPDATE news
                    SET tickers         = $1::text[],
                        context_orgs    = $2::text[],
                        sentiment_score = $3,
                        sentiment_label = $4
                    WHERE id = $5
                    """,
                    result.tickers,
                    result.context_orgs,
                    result.sentiment_score,
                    result.sentiment_label,
                    article["id"],
                )

        logger.info("news_scrape_ner_complete", articles_processed=len(newly_inserted))


async def job_daily_macro() -> None:
    """Daily macro indicators at 02:00 — FX rate + policy rate check."""
    from db.pool import get_pool
    from extraction.registry import STREAMS
    from extraction.jobs import job_run

    async with job_run("daily_macro") as ctx:
        pool = await get_pool()
        total = 0
        for stream_name in ("macro_usd_bdt", "macro_policy_rate"):
            try:
                result = await STREAMS[stream_name].fetch()
                n = await _upsert_macro_df(pool, result.data)
                total += n
                logger.info("daily_macro_stream_done", stream=stream_name, upserted=n)
            except Exception as exc:
                logger.warning("daily_macro_stream_failed", stream=stream_name, error=str(exc))
        ctx["records_inserted"] = total


async def job_weekly_fundamentals() -> None:
    """Weekly fundamental scrape (Sunday 23:00) — multi-year EPS/NAV/PE/div for all tickers.

    ~18 min for 406 tickers at 3 concurrent, 1.5s delay.
    """
    from extraction.bulk_load.fundamentals_historical_loader import bulk_load_fundamentals_historical
    logger.info("job_weekly_fundamentals: starting")
    summary = await bulk_load_fundamentals_historical()
    logger.info(
        "job_weekly_fundamentals: complete",
        ok=summary["ok"],
        failed=summary["failed"],
        upserted=summary["total_upserted"],
    )


async def job_monthly() -> None:
    """Monthly macro data (1st day, 01:00) — all 5 macro streams."""
    from db.pool import get_pool
    from extraction.registry import STREAMS
    from extraction.jobs import job_run

    async with job_run("monthly_macro") as ctx:
        pool = await get_pool()
        total = 0
        for stream_name in (
            "macro_policy_rate", "macro_cpi", "macro_usd_bdt",
            "macro_gdp", "macro_remittance",
        ):
            try:
                result = await STREAMS[stream_name].fetch()
                n = await _upsert_macro_df(pool, result.data)
                total += n
                logger.info("monthly_macro_stream_done", stream=stream_name, upserted=n)
            except Exception as exc:
                logger.warning("monthly_macro_stream_failed", stream=stream_name, error=str(exc))
        ctx["records_inserted"] = total


async def job_quarterly() -> None:
    """Quarterly ML retrain (Jan/Apr/Jul/Oct 1st, 03:00 BD time).

    1. Catch up any unevaluated prediction outcomes.
    2. Check accuracy thresholds — fire alert if degraded.
    3. Retrain XGBoost + LSTM on expanded dataset.
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
        logger.info("quarterly_retrain: outcomes populated: %d", n_outcomes)

        # ── 2. Accuracy check + alert ──────────────────────────────────
        check = await check_accuracy_thresholds(pool)
        if check["alert_level"]:
            logger.warning(
                "quarterly_retrain: accuracy degraded level=%s horizon=%s accuracy=%s",
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
                shutil.copy(
                    versioned_dir / "fundamental_scorer.pkl",
                    current_dir / "fundamental_scorer.pkl",
                )
                logger.info("quarterly_retrain: XGBoost retrained on %d samples", len(X))
            else:
                logger.warning("quarterly_retrain: XGBoost skipped — only %d samples", len(X))
        except Exception as exc:
            logger.error("quarterly_retrain: XGBoost failed: %s", exc)
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
                    batch_size=64, shuffle=True,
                )
                val_dl = DataLoader(
                    TensorDataset(
                        torch.from_numpy(X_arr[split:]),
                        torch.from_numpy(y_arr[split:]),
                    ),
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
                logger.info("quarterly_retrain: LSTM retrained best_val_loss=%.4f", best_val_loss)
            else:
                logger.warning("quarterly_retrain: LSTM skipped — only %d sequences", len(X_arr))
        except Exception as exc:
            logger.error("quarterly_retrain: LSTM failed: %s", exc)
            errors.append(f"LSTM: {exc}")

        # ── 5. Summary alert ───────────────────────────────────────────
        await fire_alert(
            severity="INFO",
            stream_name="ml_predictions",
            message=f"Quarterly ML retrain complete (version={version})",
            details={"version": version, "errors": errors, "accuracy_check": check},
        )

        ctx["records_inserted"] = n_outcomes
        logger.info("quarterly_retrain: complete version=%s errors=%s", version, errors)


async def job_health_checks() -> None:
    """6-hourly source health checks — populates source_health + fires alerts."""
    from extraction.observability import run_health_checks
    logger.info("job_health_checks: starting")
    await run_health_checks()
    logger.info("job_health_checks: complete")


async def job_nightly_ml() -> None:
    """
    Nightly ML inference pipeline (~22:00 BD time, after EOD snapshot).

    Steps:
    1. Fundamental scoring (XGBoost) → stock_scores.fundamental_score
    2. Price direction (LSTM) → ml_predictions
    3. DCF valuation → stock_scores.valuation_score + health_score
    """
    from pathlib import Path
    from datetime import datetime, timezone
    from db.pool import get_pool
    from extraction.jobs import job_run

    async with job_run("nightly_ml") as ctx:
        pool = await get_pool()
        fund_scores: dict = {}

        # ── 1. Fundamental scoring ──────────────────────────────────────
        fund_path = Path("models/v1/fundamental_scorer.pkl")
        if fund_path.exists():
            from ml.models.fundamental_scorer import FundamentalScorer
            from ml.inference.score_fundamentals import score_all_tickers, write_scores

            scorer = FundamentalScorer()
            scorer.load(fund_path)
            scored_at = datetime.now(timezone.utc)
            fund_scores = await score_all_tickers(pool, scorer)
            await write_scores(pool, fund_scores, scored_at)
            logger.info("nightly_ml: fundamental scoring done n=%d", len(fund_scores))
            ctx["records_inserted"] = len(fund_scores)
        else:
            logger.warning("nightly_ml: fundamental_scorer.pkl not found — skipping")

        # ── 2. LSTM price direction ────────────────────────────────────
        lstm_path = Path("models/v1/lstm_v0.pt")
        if lstm_path.exists():
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
                    logger.warning("nightly_ml: lstm skip ticker=%s error=%s", row["ticker"], exc)
            logger.info("nightly_ml: LSTM inference done n=%d", ok)
        else:
            logger.warning("nightly_ml: lstm_v0.pt not found — skipping")

        # ── 3. DCF valuation + health score ───────────────────────────
        from ml.valuation.dcf import DCFCalculator
        from ml.scoring.health_score import compute_health_score

        tickers = await pool.fetch(
            "SELECT ticker FROM companies WHERE is_active = true"
        )
        now = datetime.now(timezone.utc)
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
                        valuation_score, health, ticker,
                    )
            except Exception as exc:
                logger.warning("nightly_ml: dcf skip ticker=%s error=%s", ticker, exc)

        logger.info("nightly_ml: complete")


async def job_news_sentiment() -> None:
    """Score unscored news articles with Gemini Flash sentiment analysis."""
    import logging as _logging
    from db.pool import get_pool
    from extraction.jobs import job_run
    from mgmt.config import get_settings

    _log = _logging.getLogger(__name__)
    settings = get_settings()
    pool = await get_pool()

    async with job_run(pool, "news_sentiment", "news_en"):
        from chat.agent import StockAnalystAgent
        from chat.sentiment import score_new_articles
        agent = StockAnalystAgent(
            provider=settings.chat_agent_provider,
            model=settings.chat_agent_model,
        )
        n = await score_new_articles(pool, agent._base_llm, limit=200)
        _log.info("job_news_sentiment done scored=%d", n)


# ── Scheduler Configuration ────────────────────────────────────────────


def configure_scheduler(scheduler: AsyncIOScheduler) -> None:
    """Register all scheduled jobs. Timings come from mgmt.config.Settings."""
    from mgmt.config import get_settings
    cfg = get_settings()

    if cfg.pipeline_test_mode:
        _configure_test_mode(scheduler, cfg)
    else:
        _configure_production_mode(scheduler, cfg)


def _configure_production_mode(scheduler: AsyncIOScheduler, cfg: object) -> None:
    """Production schedule: real cron/interval timings."""
    scheduler.add_job(
        job_live_prices,
        trigger="cron",
        day_of_week=cfg.live_prices_market_days,
        hour=f"{cfg.live_prices_market_open_hour}-{cfg.live_prices_market_close_hour}",
        minute=cfg.live_prices_minutes,
        id="live_price_pull",
        replace_existing=True,
    )

    scheduler.add_job(
        job_eod_snapshot,
        trigger="cron",
        day_of_week=cfg.live_prices_market_days,
        hour=cfg.eod_snapshot_hour,
        minute=cfg.eod_snapshot_minute,
        id="eod_snapshot",
        replace_existing=True,
    )

    scheduler.add_job(
        job_announcements,
        trigger="interval",
        hours=cfg.announcements_interval_hours,
        id="dse_announcements",
        replace_existing=True,
    )

    scheduler.add_job(
        job_daily_macro,
        trigger="cron",
        hour=cfg.daily_macro_hour,
        minute=cfg.daily_macro_minute,
        id="daily_macro",
        replace_existing=True,
    )

    scheduler.add_job(
        job_weekly_fundamentals,
        trigger="cron",
        day_of_week=cfg.weekly_fundamentals_day,
        hour=cfg.weekly_fundamentals_hour,
        minute=cfg.weekly_fundamentals_minute,
        id="weekly_fundamentals",
        replace_existing=True,
    )

    scheduler.add_job(
        job_monthly,
        trigger="cron",
        day=cfg.monthly_day,
        hour=cfg.monthly_hour,
        minute=cfg.monthly_minute,
        id="monthly",
        replace_existing=True,
    )

    scheduler.add_job(
        job_quarterly,
        trigger="cron",
        month=cfg.quarterly_months,
        day=cfg.quarterly_day,
        hour=cfg.quarterly_hour,
        minute=cfg.quarterly_minute,
        id="quarterly_retrain",
        replace_existing=True,
    )

    scheduler.add_job(
        job_health_checks,
        trigger="interval",
        hours=cfg.health_check_interval_hours,
        id="health_checks",
        replace_existing=True,
    )

    scheduler.add_job(
        job_news_scrape,
        trigger="interval",
        hours=cfg.news_scrape_interval_hours,
        id="news_scrape",
        replace_existing=True,
    )

    scheduler.add_job(
        job_nightly_ml,
        trigger="cron",
        hour=22,
        minute=0,
        timezone=BD_TZ,
        id="nightly_ml",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        job_news_sentiment,
        "cron",
        id="news_sentiment",
        hour=3,
        minute=30,
        timezone=BD_TZ,
        replace_existing=True,
        misfire_grace_time=3600,
    )

    logger.info("scheduler: all 11 jobs registered (production mode)")


def _configure_test_mode(scheduler: AsyncIOScheduler, cfg: object) -> None:
    """
    Test mode: compress all intervals to minutes so a week of runs completes in ~1h.
    Simulated cadence per 60 min:
      live_prices       every 2min  → ~30 runs  (prod: ~19/market-day)
      eod_snapshot      every 5min  → ~12 runs  (prod: 1/day)
      announcements     every 5min  → ~12 runs  (prod: 12/day)
      daily_macro       every 7min  → ~8 runs   (prod: 1/day)
      weekly_fundam.    every 10min → ~6 runs   (prod: 1/week)
      monthly           every 15min → ~4 runs   (prod: 1/month)
      quarterly_retrain every 20min → ~3 runs   (prod: 1/quarter)
      health_checks     every 5min  → ~12 runs  (prod: 4/day)
    """
    job_map = [
        (job_live_prices,         "live_price_pull",     cfg.test_live_prices_minutes),
        (job_eod_snapshot,        "eod_snapshot",        cfg.test_eod_snapshot_minutes),
        (job_announcements,       "dse_announcements",   cfg.test_announcements_minutes),
        (job_news_scrape,         "news_scrape",         cfg.test_news_scrape_minutes),
        (job_daily_macro,         "daily_macro",         cfg.test_daily_macro_minutes),
        (job_weekly_fundamentals, "weekly_fundamentals", cfg.test_weekly_fundamentals_minutes),
        (job_monthly,             "monthly",             cfg.test_monthly_minutes),
        (job_quarterly,           "quarterly_retrain",   cfg.test_quarterly_minutes),
        (job_health_checks,       "health_checks",       cfg.test_health_check_minutes),
        (job_nightly_ml,          "nightly_ml",          cfg.test_nightly_ml_minutes),
        (job_news_sentiment,      "news_sentiment",      cfg.test_news_sentiment_minutes),
    ]
    for func, job_id, interval_minutes in job_map:
        scheduler.add_job(
            func,
            trigger="interval",
            minutes=interval_minutes,
            id=job_id,
            replace_existing=True,
        )

    logger.warning(
        "scheduler: TEST MODE — all 10 intervals compressed to minutes. "
        "Do NOT use in production."
    )


async def start_scheduler(scheduler: AsyncIOScheduler) -> None:
    """Start the scheduler."""
    configure_scheduler(scheduler)
    scheduler.start()
    logger.info("scheduler: started")


async def stop_scheduler(scheduler: AsyncIOScheduler) -> None:
    """Stop the scheduler gracefully."""
    scheduler.shutdown(wait=True)
    logger.info("scheduler: stopped")


if __name__ == "__main__":
    import asyncio
    import signal

    from mgmt.config import get_settings

    settings = get_settings()
    sync_url = (
        settings.database_url
        .replace("postgresql+asyncpg://", "postgresql+psycopg://")
        .replace("postgresql://", "postgresql+psycopg://")
    )

    sched = get_scheduler(sync_url)

    async def _run() -> None:
        configure_scheduler(sched)
        sched.start()
        logger.info("scheduler: running — press Ctrl+C to stop")
        stop_event = asyncio.Event()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)

        await stop_event.wait()
        sched.shutdown(wait=True)
        logger.info("scheduler: shutdown complete")

    logging.basicConfig(level=settings.log_level.upper())
    asyncio.run(_run())
