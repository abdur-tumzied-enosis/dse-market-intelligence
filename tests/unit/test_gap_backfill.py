"""Tests for daily-bar gap backfill: detection, row building, orchestrator."""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pandas as pd

from extraction.gap_backfill import find_missing_dates, rows_from_frame
from mgmt.config import get_settings


def test_gap_backfill_settings_defaults():
    cfg = get_settings()
    assert cfg.gap_backfill_enabled is True
    assert cfg.gap_backfill_window_days == 30
    assert cfg.gap_backfill_hour == 18
    assert cfg.gap_backfill_minute == 0


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


INGESTED = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)


def _frame(records: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(records)


def test_rows_from_frame_full_ohlcv():
    df = _frame([{
        "ticker": "GP", "date": datetime(2026, 6, 8, tzinfo=UTC),
        "open": Decimal("310"), "high": Decimal("315"), "low": Decimal("308"),
        "close": Decimal("312"), "volume": 1000, "trades": 50,
        "value_bdt": Decimal("312000"), "source": "bdshare_historical",
    }])
    rows = rows_from_frame(df, {date(2026, 6, 8)}, INGESTED)
    assert len(rows) == 1
    r = rows[0]
    assert r[0] == datetime(2026, 6, 8, tzinfo=UTC)   # time
    assert r[1] == "GP"                               # ticker
    assert r[5] == Decimal("312")                     # close
    assert r[6] == 1000 and r[7] == 50                # volume, trades
    assert r[13] == "ok"                              # quality_flag


def test_rows_from_frame_filters_dates_outside_missing():
    df = _frame([
        {"ticker": "GP", "date": datetime(2026, 6, 8, tzinfo=UTC),
         "close": Decimal("312"), "source": "bdshare_historical"},
        {"ticker": "GP", "date": datetime(2026, 6, 9, tzinfo=UTC),
         "close": Decimal("313"), "source": "bdshare_historical"},
    ])
    rows = rows_from_frame(df, {date(2026, 6, 9)}, INGESTED)
    assert len(rows) == 1
    assert rows[0][0] == datetime(2026, 6, 9, tzinfo=UTC)


def test_rows_from_frame_no_close_uses_mid_and_no_ohlc_flag():
    # amarstock_historical bars: high/low only
    df = _frame([{
        "ticker": "GP", "date": datetime(2026, 6, 8, tzinfo=UTC),
        "high": Decimal("316"), "low": Decimal("308"),
        "volume": 500, "source": "amarstock_historical",
    }])
    rows = rows_from_frame(df, {date(2026, 6, 8)}, INGESTED)
    assert len(rows) == 1
    assert rows[0][5] == Decimal("312")               # (316+308)/2
    assert rows[0][13] == "no_ohlc"


def test_rows_from_frame_drops_rows_without_any_price():
    df = _frame([{
        "ticker": "GP", "date": datetime(2026, 6, 8, tzinfo=UTC),
        "volume": 500, "source": "amarstock_historical",
    }])
    assert rows_from_frame(df, {date(2026, 6, 8)}, INGESTED) == []


def test_rows_from_frame_nan_volume_becomes_none():
    df = _frame([{
        "ticker": "GP", "date": datetime(2026, 6, 8, tzinfo=UTC),
        "close": Decimal("312"), "volume": float("nan"),
        "source": "bdshare_historical",
    }])
    rows = rows_from_frame(df, {date(2026, 6, 8)}, INGESTED)
    assert rows[0][6] is None


def test_rows_from_frame_empty_frame():
    assert rows_from_frame(pd.DataFrame(), {date(2026, 6, 8)}, INGESTED) == []
