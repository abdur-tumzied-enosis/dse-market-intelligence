"""Crash / downtime recovery for the extraction scheduler.

On boot, recover_missed_jobs() converges the pipeline to the state it should be
in: anchored daily/EOD jobs that were due earlier today but never ran are run
once (catch-up), and live-price polling gaps are recorded to market_gaps.

Pure decision functions (should_catch_up, detect_gap) carry the logic and are
unit-tested offline; DB access goes through _get_pool() so it can be patched.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import pytz

logger = logging.getLogger(__name__)
BD_TZ = pytz.timezone("Asia/Dhaka")


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
