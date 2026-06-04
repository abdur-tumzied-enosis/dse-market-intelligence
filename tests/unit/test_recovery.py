"""Tests for crash/downtime recovery: reconciler + gap detection."""
from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

import pytest
import pytz

from extraction.recovery import (
    CATCHUP_REGISTRY,
    CatchUpJob,
    _is_every_day,
    detect_gap,
    maybe_record_intraday_gap,
    recover_missed_jobs,
    should_catch_up,
)
from mgmt.config import get_settings

BD_TZ = pytz.timezone("Asia/Dhaka")


def test_recovery_settings_defaults():
    cfg = get_settings()
    assert cfg.recovery_enabled is True
    assert cfg.intraday_gap_threshold_minutes == 3


# ── should_catch_up ────────────────────────────────────────────────────
def _bd(y, m, d, hh, mm):
    return BD_TZ.localize(datetime(y, m, d, hh, mm))


def test_should_catch_up_overdue_and_never_ran():
    now = _bd(2026, 6, 4, 23, 0)          # 23:00 BD
    # anchor 22:00, run day, no success today
    assert should_catch_up(now, 22, 0, True, None) is True


def test_should_catch_up_already_ran_today():
    now = _bd(2026, 6, 4, 23, 0)
    assert should_catch_up(now, 22, 0, True, date(2026, 6, 4)) is False


def test_should_catch_up_not_yet_due():
    now = _bd(2026, 6, 4, 21, 0)          # before 22:00 anchor
    assert should_catch_up(now, 22, 0, True, None) is False


def test_should_catch_up_not_a_run_day():
    now = _bd(2026, 6, 4, 23, 0)
    assert should_catch_up(now, 22, 0, False, None) is False


def test_should_catch_up_ran_on_a_previous_day():
    now = _bd(2026, 6, 4, 23, 0)
    assert should_catch_up(now, 22, 0, True, date(2026, 6, 3)) is True


# ── detect_gap ─────────────────────────────────────────────────────────
def test_detect_gap_returns_window_when_stale():
    last = datetime(2026, 6, 4, 6, 0, tzinfo=UTC)
    now = datetime(2026, 6, 4, 6, 10, tzinfo=UTC)     # 10 min later
    assert detect_gap(last, now, 3, True) == (last, now)


def test_detect_gap_none_within_threshold():
    last = datetime(2026, 6, 4, 6, 0, tzinfo=UTC)
    now = datetime(2026, 6, 4, 6, 2, tzinfo=UTC)      # 2 min < 3
    assert detect_gap(last, now, 3, True) is None


def test_detect_gap_none_when_market_closed():
    last = datetime(2026, 6, 4, 6, 0, tzinfo=UTC)
    now = datetime(2026, 6, 4, 6, 30, tzinfo=UTC)
    assert detect_gap(last, now, 3, False) is None


def test_detect_gap_none_when_no_prior_snapshot():
    now = datetime(2026, 6, 4, 6, 30, tzinfo=UTC)
    assert detect_gap(None, now, 3, True) is None


def test_detect_gap_none_exactly_at_threshold():
    """Exactly at threshold is not a gap (strict >)."""
    last = datetime(2026, 6, 4, 6, 0, tzinfo=UTC)
    now = datetime(2026, 6, 4, 6, 3, tzinfo=UTC)  # exactly 3 min
    assert detect_gap(last, now, 3, True) is None


def test_should_catch_up_exactly_at_anchor():
    """Due the instant the anchor passes (>=)."""
    now = _bd(2026, 6, 4, 22, 0)
    assert should_catch_up(now, 22, 0, True, None) is True


def test_registry_job_names_unique():
    names = [j.job_name for j in CATCHUP_REGISTRY]
    assert len(names) == len(set(names))


def test_registry_covers_expected_jobs():
    names = {j.job_name for j in CATCHUP_REGISTRY}
    assert names == {
        "daily_macro", "news_sentiment", "seed_companies", "eod_snapshot",
        "sector_pe", "nightly_ml", "weekly_fundamentals", "monthly", "quarterly",
    }


def test_run_day_market_days_excludes_friday_saturday():
    entry = next(j for j in CATCHUP_REGISTRY if j.job_name == "eod_snapshot")
    fri = BD_TZ.localize(datetime(2026, 6, 5, 15, 0))   # Friday
    sat = BD_TZ.localize(datetime(2026, 6, 6, 15, 0))   # Saturday
    sun = BD_TZ.localize(datetime(2026, 6, 7, 15, 0))   # Sunday
    assert entry.run_day(fri) is False
    assert entry.run_day(sat) is False
    assert entry.run_day(sun) is True


def test_run_day_quarterly_only_quarter_starts():
    entry = next(j for j in CATCHUP_REGISTRY if j.job_name == "quarterly")
    assert entry.run_day(BD_TZ.localize(datetime(2026, 7, 1, 4, 0))) is True
    assert entry.run_day(BD_TZ.localize(datetime(2026, 7, 2, 4, 0))) is False
    assert entry.run_day(BD_TZ.localize(datetime(2026, 6, 1, 4, 0))) is False


@pytest.mark.asyncio
async def test_maybe_record_intraday_gap_inserts_and_alerts():
    # last snapshot 10 min ago → gap; market Open
    last = datetime(2026, 6, 4, 6, 0, tzinfo=UTC)
    pool = AsyncMock()
    pool.fetchrow.side_effect = [
        {"t": last},   # MAX(time) lookup
        None,          # dedup guard: no existing unrecovered row
    ]
    now_bd = BD_TZ.localize(datetime(2026, 6, 4, 12, 10))  # 06:10 UTC

    with patch("extraction.recovery._get_pool", return_value=pool), \
         patch("extraction.recovery.get_market_status",
               new=AsyncMock(return_value={"status": "Open"})), \
         patch("extraction.recovery.fire_alert", new=AsyncMock()) as alert:
        recorded = await maybe_record_intraday_gap(now_bd)

    assert recorded is True
    insert_calls = [c for c in pool.execute.await_args_list
                    if "market_gaps" in c.args[0]]
    assert len(insert_calls) == 1
    alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_maybe_record_intraday_gap_noop_when_closed():
    pool = AsyncMock()
    now_bd = BD_TZ.localize(datetime(2026, 6, 4, 18, 0))
    with patch("extraction.recovery._get_pool", return_value=pool), \
         patch("extraction.recovery.get_market_status",
               new=AsyncMock(return_value={"status": "Closed"})):
        recorded = await maybe_record_intraday_gap(now_bd)
    assert recorded is False
    pool.execute.assert_not_awaited()
    pool.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_recover_runs_overdue_job_not_done_today():
    """daily_macro (anchor 02:00) at 23:00 BD with no success today → runs once."""
    pool = AsyncMock()
    pool.fetchrow.return_value = {"d": None}      # never succeeded → last_success None
    ran: list[str] = []

    async def fake_macro():
        ran.append("daily_macro")

    now_bd = BD_TZ.localize(datetime(2026, 6, 4, 23, 0))
    entry = CatchUpJob("daily_macro", fake_macro, 2, 0, _is_every_day, 0)

    with patch("extraction.recovery._get_pool", return_value=pool), \
         patch("extraction.recovery.CATCHUP_REGISTRY", [entry]), \
         patch("extraction.recovery._now_bd", return_value=now_bd), \
         patch("extraction.recovery.maybe_record_intraday_gap",
               new=AsyncMock(return_value=False)), \
         patch("extraction.recovery._maybe_resume_live_prices", new=AsyncMock()):
        summary = await recover_missed_jobs()

    assert ran == ["daily_macro"]
    assert summary["ran"] == 1


@pytest.mark.asyncio
async def test_recover_skips_job_already_done_today():
    pool = AsyncMock()
    pool.fetchrow.return_value = {"d": date(2026, 6, 4)}   # ran today
    ran: list[str] = []

    async def fake_macro():
        ran.append("daily_macro")

    now_bd = BD_TZ.localize(datetime(2026, 6, 4, 23, 0))
    entry = CatchUpJob("daily_macro", fake_macro, 2, 0, _is_every_day, 0)

    with patch("extraction.recovery._get_pool", return_value=pool), \
         patch("extraction.recovery.CATCHUP_REGISTRY", [entry]), \
         patch("extraction.recovery._now_bd", return_value=now_bd), \
         patch("extraction.recovery.maybe_record_intraday_gap",
               new=AsyncMock(return_value=False)), \
         patch("extraction.recovery._maybe_resume_live_prices", new=AsyncMock()):
        summary = await recover_missed_jobs()

    assert ran == []
    assert summary["skipped"] == 1


@pytest.mark.asyncio
async def test_recover_disabled_is_noop():
    with patch("extraction.recovery.get_settings") as gs:
        gs.return_value.recovery_enabled = False
        summary = await recover_missed_jobs()
    assert summary["checked"] == 0


@pytest.mark.asyncio
async def test_recover_one_failing_job_does_not_stop_others():
    pool = AsyncMock()
    pool.fetchrow.return_value = {"d": None}
    ran: list[str] = []

    async def boom():
        raise RuntimeError("kaboom")

    async def ok():
        ran.append("ok")

    now_bd = BD_TZ.localize(datetime(2026, 6, 4, 23, 0))
    registry = [
        CatchUpJob("boom", boom, 2, 0, _is_every_day, 0),
        CatchUpJob("ok", ok, 2, 0, _is_every_day, 1),
    ]
    with patch("extraction.recovery._get_pool", return_value=pool), \
         patch("extraction.recovery.CATCHUP_REGISTRY", registry), \
         patch("extraction.recovery._now_bd", return_value=now_bd), \
         patch("extraction.recovery.maybe_record_intraday_gap",
               new=AsyncMock(return_value=False)), \
         patch("extraction.recovery._maybe_resume_live_prices", new=AsyncMock()):
        summary = await recover_missed_jobs()

    assert ran == ["ok"]
    assert summary["ran"] == 1
    assert summary["failed"] == 1
