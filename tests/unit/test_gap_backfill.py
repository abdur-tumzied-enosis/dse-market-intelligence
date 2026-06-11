"""Tests for daily-bar gap backfill: detection, row building, orchestrator."""
from __future__ import annotations

from mgmt.config import get_settings


def test_gap_backfill_settings_defaults():
    cfg = get_settings()
    assert cfg.gap_backfill_enabled is True
    assert cfg.gap_backfill_window_days == 30
    assert cfg.gap_backfill_hour == 18
    assert cfg.gap_backfill_minute == 0


from datetime import date

from extraction.gap_backfill import find_missing_dates

# 2026-06-11 is a Thursday. Window [today-7, today-1] = Jun 4 (Thu) … Jun 10 (Wed).
# Trading dates in it: Jun 4 (Thu), 7 (Sun), 8 (Mon), 9 (Tue), 10 (Wed).
TODAY = date(2026, 6, 11)
WINDOW_TRADING_DATES = [
    date(2026, 6, 4), date(2026, 6, 7), date(2026, 6, 8),
    date(2026, 6, 9), date(2026, 6, 10),
]


def test_find_missing_all_absent():
    assert find_missing_dates(set(), set(), TODAY, 7) == WINDOW_TRADING_DATES


def test_find_missing_excludes_friday_saturday():
    missing = find_missing_dates(set(), set(), TODAY, 7)
    assert date(2026, 6, 5) not in missing   # Friday
    assert date(2026, 6, 6) not in missing   # Saturday


def test_find_missing_excludes_today():
    missing = find_missing_dates(set(), set(), TODAY, 7)
    assert TODAY not in missing


def test_find_missing_none_when_all_present():
    assert find_missing_dates(set(WINDOW_TRADING_DATES), set(), TODAY, 7) == []


def test_find_missing_excludes_skip_list():
    skip = {date(2026, 6, 8)}     # recorded holiday
    missing = find_missing_dates(set(), skip, TODAY, 7)
    assert date(2026, 6, 8) not in missing
    assert date(2026, 6, 7) in missing


def test_find_missing_window_start_inclusive():
    # window_days=7 → start = Jun 4, which is a trading Thursday
    missing = find_missing_dates(set(), set(), TODAY, 7)
    assert missing[0] == date(2026, 6, 4)


def test_find_missing_partial():
    present = {date(2026, 6, 4), date(2026, 6, 10)}
    missing = find_missing_dates(present, set(), TODAY, 7)
    assert missing == [date(2026, 6, 7), date(2026, 6, 8), date(2026, 6, 9)]
