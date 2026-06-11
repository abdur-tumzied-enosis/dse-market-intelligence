"""Crash / downtime recovery for the extraction scheduler.

On boot, recover_missed_jobs() converges the pipeline to the state it should be
in: anchored daily/EOD jobs that were due earlier today but never ran are run
once (catch-up), and live-price polling gaps are recorded to market_gaps.

Pure decision functions (should_catch_up, detect_gap) carry the logic and are
unit-tested offline; DB access goes through _get_pool() so it can be patched.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

import pytz

from extraction.market_status import get_market_status
from extraction.observability import fire_alert
from mgmt.config import get_settings

logger = logging.getLogger(__name__)
BD_TZ = pytz.timezone("Asia/Dhaka")


async def _get_pool() -> asyncpg.Pool:
    """Indirection so tests can patch the DB pool without a live database."""
    from db.pool import get_pool
    return await get_pool()

# Python weekday(): Mon=0..Sun=6. DSE trades Sun–Thu → {6,0,1,2,3}.
_MARKET_WEEKDAYS = {6, 0, 1, 2, 3}


def _is_every_day(now: datetime) -> bool:
    return True


def _is_market_day(now: datetime) -> bool:
    return now.weekday() in _MARKET_WEEKDAYS


def _is_sunday(now: datetime) -> bool:
    return now.weekday() == 6


def _is_month_start(now: datetime) -> bool:
    return now.day == 1


def _is_quarter_start(now: datetime) -> bool:
    return now.month in (1, 4, 7, 10) and now.day == 1


@dataclass(frozen=True)
class CatchUpJob:
    job_name: str                              # must match the job_run() name
    func: Callable[[], Awaitable[None]]        # the scheduler job coroutine
    anchor_hour: int
    anchor_minute: int
    run_day: Callable[[datetime], bool]
    dep_rank: int                              # lower runs first


# ── Pure decision functions (no DB) ─────────────────────────────────────

def _skip_reason(
    now: datetime,
    anchor_hour: int,
    anchor_minute: int,
    is_run_day: bool,
    last_success_date: date | None,
) -> str | None:
    """Why a job should NOT be caught up, or None if it should run. The three
    skip conditions, in the same order should_catch_up evaluates them."""
    if not is_run_day:
        return "not a run-day today"
    anchor = now.replace(hour=anchor_hour, minute=anchor_minute, second=0, microsecond=0)
    if now < anchor:
        return f"not due yet (anchor {anchor_hour:02d}:{anchor_minute:02d})"
    if last_success_date == now.date():
        return "already succeeded today"
    return None


def should_catch_up(
    now: datetime,
    anchor_hour: int,
    anchor_minute: int,
    is_run_day: bool,
    last_success_date: date | None,
) -> bool:
    """True when a job was due earlier today on a valid run-day but has no
    successful run recorded for today's date.

    now must be tz-aware (BD). last_success_date is the BD date of the job's most
    recent successful run, or None if it has never succeeded.
    """
    return _skip_reason(now, anchor_hour, anchor_minute, is_run_day, last_success_date) is None


def detect_gap(
    last_snapshot_time: datetime | None,
    now_utc: datetime,
    threshold_minutes: int,
    market_open: bool,
) -> tuple[datetime, datetime] | None:
    """Return (gap_start, gap_end) when the market is open and the most recent
    intraday snapshot is older than threshold_minutes; else None.

    None when the market is closed or there is no prior snapshot (first poll of
    the day has no baseline to measure a gap against).
    """
    if not market_open or last_snapshot_time is None:
        return None
    if now_utc.tzinfo is None or last_snapshot_time.tzinfo is None:
        raise ValueError("detect_gap requires tz-aware datetimes")
    if now_utc - last_snapshot_time > timedelta(minutes=threshold_minutes):
        return (last_snapshot_time, now_utc)
    return None


def _build_registry() -> list[CatchUpJob]:
    from extraction.scheduler import (  # noqa: PLC0415
        job_daily_macro,
        job_eod_snapshot,
        job_monthly,
        job_news_sentiment,
        job_nightly_ml,
        job_price_gap_backfill,
        job_quarterly,
        job_sector_pe,
        job_seed_companies,
        job_weekly_fundamentals,
    )

    cfg = get_settings()
    return [
        CatchUpJob("daily_macro", job_daily_macro,
                   cfg.daily_macro_hour, cfg.daily_macro_minute, _is_every_day, 0),
        CatchUpJob("news_sentiment", job_news_sentiment,
                   3, 30, _is_every_day, 0),
        CatchUpJob("seed_companies", job_seed_companies,
                   8, 0, _is_every_day, 1),
        CatchUpJob("eod_snapshot", job_eod_snapshot,
                   cfg.eod_snapshot_hour, cfg.eod_snapshot_minute, _is_market_day, 2),
        CatchUpJob("sector_pe", job_sector_pe,
                   15, 45, _is_market_day, 3),
        CatchUpJob("price_gap_backfill", job_price_gap_backfill,
                   cfg.gap_backfill_hour, cfg.gap_backfill_minute, _is_every_day, 3),
        CatchUpJob("nightly_ml", job_nightly_ml,
                   22, 0, _is_every_day, 4),
        CatchUpJob("weekly_fundamentals", job_weekly_fundamentals,
                   cfg.weekly_fundamentals_hour, cfg.weekly_fundamentals_minute,
                   _is_sunday, 5),
        CatchUpJob("monthly", job_monthly,
                   cfg.monthly_hour, cfg.monthly_minute, _is_month_start, 5),
        CatchUpJob("quarterly", job_quarterly,
                   cfg.quarterly_hour, cfg.quarterly_minute, _is_quarter_start, 5),
    ]


CATCHUP_REGISTRY: list[CatchUpJob] = _build_registry()


def _today_utc_start(now_bd: datetime) -> datetime:
    """00:00 UTC of the current Dhaka date — lower bound for today's intraday rows."""
    d = now_bd.date()
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


async def _record_gap(
    pool: asyncpg.Pool, session_date: date, gap_start: datetime, gap_end: datetime, reason: str
) -> bool:
    """Insert a market_gaps row unless an unrecovered row with the same
    (session_date, gap_start) already exists. Returns True if a row was inserted."""
    existing = await pool.fetchrow(
        "SELECT 1 FROM market_gaps "
        "WHERE session_date = $1 AND gap_start = $2 AND recovered = FALSE",
        session_date, gap_start,
    )
    if existing:
        return False
    await pool.execute(
        "INSERT INTO market_gaps (session_date, gap_start, gap_end, reason) "
        "VALUES ($1, $2, $3, $4)",
        session_date, gap_start, gap_end, reason,
    )
    return True


async def maybe_record_intraday_gap(now_bd: datetime) -> bool:
    """If the market is open and intraday_prices has no recent row, record a
    market_gaps entry and fire a WARNING alert. Returns True if a gap was
    recorded. Never raises — recovery must not crash the caller.
    """
    try:
        if (await get_market_status())["status"] != "Open":
            return False
        pool = await _get_pool()
        threshold = get_settings().intraday_gap_threshold_minutes

        row = await pool.fetchrow(
            "SELECT MAX(time) AS t FROM intraday_prices WHERE time >= $1",
            _today_utc_start(now_bd),
        )
        last_t = row["t"] if row else None
        now_utc = now_bd.astimezone(UTC)
        gap = detect_gap(last_t, now_utc, threshold, True)
        if gap is None:
            return False

        recorded = await _record_gap(
            pool, now_bd.date(), gap[0], gap[1], "scheduler_downtime"
        )
        if recorded:
            logger.warning("recovery: intraday gap %s..%s", gap[0], gap[1])
            await fire_alert(
                severity="WARNING",
                message=f"Intraday polling gap detected: {gap[0]} .. {gap[1]}",
                stream_name="live_prices",
                details={"gap_start": gap[0].isoformat(), "gap_end": gap[1].isoformat()},
            )
        return recorded
    except Exception as exc:
        logger.warning("recovery: gap check failed error=%s", exc, exc_info=True)
        return False


def _now_bd() -> datetime:
    """Current time in BD tz. Wrapped so tests can patch it."""
    return datetime.now(BD_TZ)


async def _last_success_date(pool: asyncpg.Pool, job_name: str) -> date | None:
    """BD date of the job's most recent successful pipeline_jobs run, or None."""
    row = await pool.fetchrow(
        "SELECT MAX((started_at AT TIME ZONE 'Asia/Dhaka')::date) AS d "
        "FROM pipeline_jobs WHERE job_name = $1 AND status = 'success'",
        job_name,
    )
    return row["d"] if row and row["d"] else None


async def _maybe_resume_live_prices(now_bd: datetime) -> None:
    """If the market is open, fire one live pull immediately so polling resumes
    without waiting for the next cron tick (also mitigates a lost open-trigger).
    The pull's own per-poll gap check records any market_gaps row."""
    try:
        if (await get_market_status())["status"] != "Open":
            return
        from extraction.scheduler import job_live_prices  # noqa: PLC0415
        logger.info("recovery: market open on boot — triggering live_prices")
        await job_live_prices()
    except Exception as exc:
        logger.warning("recovery: live resume failed error=%s", exc, exc_info=True)


async def recover_missed_jobs() -> dict[str, int]:
    """Boot-time reconciler. For each anchored job that was due earlier today on a
    valid run-day but has no success recorded today, run it once in dep_rank order.
    Then resume live polling if the market is open. Never raises."""
    summary = {"checked": 0, "ran": 0, "skipped": 0, "failed": 0}
    cfg = get_settings()
    if not cfg.recovery_enabled:
        logger.info("recovery: disabled (recovery_enabled=False)")
        return summary

    now = _now_bd()
    try:
        pool = await _get_pool()
        for job in sorted(CATCHUP_REGISTRY, key=lambda j: j.dep_rank):
            summary["checked"] += 1
            last = await _last_success_date(pool, job.job_name)
            reason = _skip_reason(now, job.anchor_hour, job.anchor_minute,
                                  job.run_day(now), last)
            if reason is not None:
                summary["skipped"] += 1
                logger.debug("recovery: skip job=%s reason=%s last_success=%s",
                             job.job_name, reason, last)
                continue
            logger.info("recovery: catching up job=%s (last_success=%s)", job.job_name, last)
            try:
                await job.func()
                summary["ran"] += 1
            except Exception as exc:
                summary["failed"] += 1
                logger.error("recovery: catch-up failed job=%s error=%s",
                             job.job_name, exc, exc_info=True)
    except Exception as exc:
        logger.error("recovery: aborted before completing error=%s", exc, exc_info=True)

    await _maybe_resume_live_prices(now)
    logger.info(
        "recovery_summary checked=%d ran=%d skipped=%d failed=%d",
        summary["checked"], summary["ran"], summary["skipped"], summary["failed"],
    )
    return summary
