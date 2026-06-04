import os

os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from datetime import UTC, datetime

import pytz

from extraction.market_status import clock_status
from extraction.scheduler import (
    _live_records_to_intraday_rows,
    _live_records_to_rows,
    _split_known_tickers,
)

BD = pytz.timezone("Asia/Dhaka")
BUCKET = BD.localize(datetime(2026, 6, 1, 0, 0, 0))
SNAP = datetime(2026, 6, 1, 5, 30, 0, tzinfo=UTC)  # real intraday instant


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


def test_split_known_tickers_drops_unknown():
    rows = _live_records_to_rows(
        [{"ticker": "CITYBANK", "close": 23.4}, {"ticker": "BDSERVICE", "close": 1.0},
         {"ticker": "GP", "close": 110.0}],
        BUCKET, "x",
    )
    kept, dropped = _split_known_tickers(rows, {"CITYBANK", "GP"})
    assert [r[1] for r in kept] == ["CITYBANK", "GP"]
    assert dropped == ["BDSERVICE"]


def test_split_known_tickers_all_known_drops_nothing():
    rows = _live_records_to_rows(
        [{"ticker": "CITYBANK", "close": 23.4}, {"ticker": "GP", "close": 110.0}],
        BUCKET, "x",
    )
    kept, dropped = _split_known_tickers(rows, {"CITYBANK", "GP"})
    assert len(kept) == 2
    assert dropped == []


def test_split_known_tickers_dedupes_and_sorts_dropped():
    rows = _live_records_to_rows(
        [{"ticker": "ZEAL", "close": 1.0}, {"ticker": "ACME", "close": 2.0},
         {"ticker": "ZEAL", "close": 1.5}],
        BUCKET, "x",
    )
    kept, dropped = _split_known_tickers(rows, set())
    assert kept == []
    assert dropped == ["ACME", "ZEAL"]


def test_intraday_row_stamps_real_instant_and_raw_cumulative():
    rows = _live_records_to_intraday_rows(
        [{"ticker": "CITYBANK", "ltp": 23.4, "volume": 1000, "trades": 10,
          "value_bdt": 28900000.0}],
        SNAP, "dse_direct_live_prices",
    )
    assert len(rows) == 1
    r = rows[0]
    # tuple order: time, ticker, ltp, cum_volume, cum_value, cum_trades, source
    assert r[0] == SNAP          # real snapshot instant, NOT a day bucket
    assert r[1] == "CITYBANK"
    assert r[2] == 23.4          # ltp
    assert r[3] == 1000          # cum_volume stored raw (no SUM)
    assert r[4] == 28900000.0    # cum_value
    assert r[5] == 10            # cum_trades


def test_intraday_close_fallback_and_value_mn_scaling():
    rows = _live_records_to_intraday_rows(
        [{"ticker": "GP", "close": 110.0, "value_mn": 5.0}],
        SNAP, "x",
    )
    assert rows[0][2] == 110.0       # ltp falls back to close
    assert rows[0][4] == 5_000_000.0  # value_mn → cum_value


def test_intraday_rows_without_price_or_ticker_skipped():
    rows = _live_records_to_intraday_rows(
        [{"ticker": "DEAD", "ltp": None, "close": None}, {"ticker": "", "ltp": 1.0}],
        SNAP, "x",
    )
    assert rows == []


def test_market_is_open_weekday_and_hours():
    assert clock_status(BD.localize(datetime(2026, 6, 1, 11, 0))) == "Open"   # Mon 11:00
    assert clock_status(BD.localize(datetime(2026, 6, 1, 15, 0))) == "Closed" # Mon 15:00
    assert clock_status(BD.localize(datetime(2026, 6, 5, 11, 0))) == "Closed" # Fri
    assert clock_status(BD.localize(datetime(2026, 6, 1, 10, 0))) == "Open"   # open boundary
    assert clock_status(BD.localize(datetime(2026, 6, 1, 9, 59))) == "Closed"
    assert clock_status(BD.localize(datetime(2026, 6, 1, 14, 30))) == "Open"  # close boundary
    assert clock_status(BD.localize(datetime(2026, 6, 1, 14, 31))) == "Closed"
    assert clock_status(BD.localize(datetime(2026, 6, 6, 11, 0))) == "Closed" # Sat
