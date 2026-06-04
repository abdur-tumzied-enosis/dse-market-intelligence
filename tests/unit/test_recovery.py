"""Tests for crash/downtime recovery: reconciler + gap detection."""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytz

from extraction.recovery import detect_gap, should_catch_up
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
