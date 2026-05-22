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
    """Register all scheduled jobs."""

    # Every 15 min during market hours (Sun–Thu 10:00–14:30)
    scheduler.add_job(
        job_live_prices,
        trigger="cron",
        day_of_week="mon,tue,wed,thu,sun",
        hour="10-14",
        minute="0,15,30,45",
        id="live_price_pull",
        replace_existing=True,
    )

    # End of day (14:35 Sun–Thu)
    scheduler.add_job(
        job_eod_snapshot,
        trigger="cron",
        day_of_week="mon,tue,wed,thu,sun",
        hour=14,
        minute=35,
        id="eod_snapshot",
        replace_existing=True,
    )

    # Every 2 hours
    scheduler.add_job(
        job_announcements,
        trigger="interval",
        hours=2,
        id="dse_announcements",
        replace_existing=True,
    )

    # Daily 02:00
    scheduler.add_job(
        job_daily_macro,
        trigger="cron",
        hour=2,
        minute=0,
        id="daily_macro",
        replace_existing=True,
    )

    # Weekly Sunday 23:00
    scheduler.add_job(
        job_weekly_fundamentals,
        trigger="cron",
        day_of_week="sun",
        hour=23,
        minute=0,
        id="weekly_fundamentals",
        replace_existing=True,
    )

    # Monthly 1st day 01:00
    scheduler.add_job(
        job_monthly,
        trigger="cron",
        day=1,
        hour=1,
        minute=0,
        id="monthly",
        replace_existing=True,
    )

    # Quarterly Jan/Apr/Jul/Oct 1st 03:00
    scheduler.add_job(
        job_quarterly,
        trigger="cron",
        month="1,4,7,10",
        day=1,
        hour=3,
        minute=0,
        id="quarterly_retrain",
        replace_existing=True,
    )

    # Every 6 hours — source health checks
    scheduler.add_job(
        job_health_checks,
        trigger="interval",
        hours=6,
        id="health_checks",
        replace_existing=True,
    )

    logger.info("scheduler: all 8 jobs registered")


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
