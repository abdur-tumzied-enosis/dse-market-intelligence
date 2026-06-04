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
from datetime import date, datetime, timedelta

import pytz

logger = logging.getLogger(__name__)
BD_TZ = pytz.timezone("Asia/Dhaka")

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
    if not is_run_day:
        return False
    anchor = now.replace(hour=anchor_hour, minute=anchor_minute, second=0, microsecond=0)
    overdue = now >= anchor
    not_done = last_success_date != now.date()
    return overdue and not_done


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
        job_quarterly,
        job_sector_pe,
        job_seed_companies,
        job_weekly_fundamentals,
    )
    from mgmt.config import get_settings  # noqa: PLC0415

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
