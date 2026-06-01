import os

os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from datetime import datetime

import pytz

from extraction.scheduler import _live_records_to_rows, _market_is_open

BD = pytz.timezone("Asia/Dhaka")
BUCKET = BD.localize(datetime(2026, 6, 1, 0, 0, 0))


def test_row_uses_ltp_as_close_and_bucket_time():
    rows = _live_records_to_rows(
        [{"ticker": "CITYBANK", "ltp": 23.4, "high": 23.9, "low": 23.1,
          "prev_close": 23.0, "change_pct": 1.74, "volume": 1000, "trades": 10,
          "value_bdt": 28900000.0}],
        BUCKET, "dse_direct_live_prices",
    )
    assert len(rows) == 1
    r = rows[0]
    # tuple order: time, ticker, open, high, low, close, volume, trades,
    #              value_bdt, prev_close, change_pct, source, quality_flag
    assert r[0] == BUCKET
    assert r[1] == "CITYBANK"
    assert r[5] == 23.4          # close = ltp
    assert r[12] == "live"


def test_change_pct_computed_when_missing():
    rows = _live_records_to_rows(
        [{"ticker": "GP", "close": 110.0, "prev_close": 100.0, "change_pct": None}],
        BUCKET, "dse_direct_live_prices",
    )
    assert rows[0][10] == 10.0   # (110-100)/100*100


def test_value_mn_fallback_scaled_to_bdt():
    rows = _live_records_to_rows(
        [{"ticker": "GP", "close": 110.0, "value_mn": 5.0}],
        BUCKET, "dse_direct_live_prices",
    )
    assert rows[0][8] == 5_000_000.0


def test_rows_without_close_are_skipped():
    rows = _live_records_to_rows(
        [{"ticker": "DEAD", "ltp": None, "close": None}, {"ticker": "", "close": 1.0}],
        BUCKET, "x",
    )
    assert rows == []


def test_market_is_open_weekday_and_hours():
    assert _market_is_open(BD.localize(datetime(2026, 6, 1, 11, 0)))         # Mon 11:00 (trading day)
    assert not _market_is_open(BD.localize(datetime(2026, 6, 1, 15, 0)))     # Mon 15:00 (after close)
    assert not _market_is_open(BD.localize(datetime(2026, 6, 5, 11, 0)))     # Fri 11:00
    assert _market_is_open(BD.localize(datetime(2026, 6, 1, 10, 0)))         # open boundary
    assert not _market_is_open(BD.localize(datetime(2026, 6, 1, 9, 59)))     # just before open
    assert _market_is_open(BD.localize(datetime(2026, 6, 1, 14, 30)))        # close boundary
    assert not _market_is_open(BD.localize(datetime(2026, 6, 1, 14, 31)))    # just after close
    assert not _market_is_open(BD.localize(datetime(2026, 6, 6, 11, 0)))     # Sat 11:00
