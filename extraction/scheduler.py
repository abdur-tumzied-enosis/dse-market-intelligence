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


# ── Job Functions ──────────────────────────────────────────────────────


async def job_live_prices() -> None:
    """Fetch live prices every 15 min during market hours."""
    logger.info("job_live_prices: starting")
    # TODO: implement ingest_live_prices()
    # TODO: update_redis_cache()
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
    """Fetch announcements and news every 2 hours."""
    logger.info("job_announcements: starting")
    # TODO: implement scrape_dse_announcements()
    # TODO: scrape_news_all_sources()
    # TODO: enqueue celery task: process_new_articles (sentiment + embedding)
    logger.info("job_announcements: complete")


async def job_daily_macro() -> None:
    """Daily macro indicators at 02:00."""
    logger.info("job_daily_macro: starting")
    # TODO: implement fetch_usd_bdt_rate()
    # TODO: check_bsec_circulars()
    # TODO: check_new_ipo_filings()
    logger.info("job_daily_macro: complete")


async def job_weekly_fundamentals() -> None:
    """Weekly fundamental scrape (Sunday 23:00).

    ~18 min for 350 pages → offload to Celery.
    """
    logger.info("job_weekly_fundamentals: enqueueing to Celery")
    # TODO: enqueue celery task: scrape_all_fundamentals
    # TODO: enqueue celery task: check_index_composition
    logger.info("job_weekly_fundamentals: queued")


async def job_monthly() -> None:
    """Monthly macro data (1st day, 01:00)."""
    logger.info("job_monthly: starting")
    # TODO: implement fetch_bangladesh_bank_rates()
    # TODO: fetch_forex_reserves()
    # TODO: enqueue celery task: recalculate_beta_all_stocks
    logger.info("job_monthly: complete")


async def job_quarterly() -> None:
    """Quarterly retrain (Jan/Apr/Jul/Oct 1st, 03:00)."""
    logger.info("job_quarterly: starting")
    # TODO: implement fetch_gdp_data()
    # TODO: enqueue celery task: retrain_ml_models
    logger.info("job_quarterly: complete")


async def job_health_checks() -> None:
    """6-hourly source health checks — populates source_health + fires alerts."""
    from extraction.observability import run_health_checks
    logger.info("job_health_checks: starting")
    await run_health_checks()
    logger.info("job_health_checks: complete")


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

    logger.info("scheduler: all 8 jobs registered (production mode)")


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
        (job_daily_macro,         "daily_macro",         cfg.test_daily_macro_minutes),
        (job_weekly_fundamentals, "weekly_fundamentals", cfg.test_weekly_fundamentals_minutes),
        (job_monthly,             "monthly",             cfg.test_monthly_minutes),
        (job_quarterly,           "quarterly_retrain",   cfg.test_quarterly_minutes),
        (job_health_checks,       "health_checks",       cfg.test_health_check_minutes),
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
        "scheduler: TEST MODE — all intervals compressed to minutes. "
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
