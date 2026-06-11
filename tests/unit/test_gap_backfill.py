"""Tests for daily-bar gap backfill: detection, row building, orchestrator."""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from extraction.gap_backfill import (
    NO_DATA_REASON,
    backfill_missing_dates,
    find_missing_dates,
    rows_from_frame,
)
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


# ── Orchestrator tests ────────────────────────────────────────────────────────


class FakePool:
    """Answers pool.fetch/fetchrow by SQL keyword; records execute/executemany calls."""

    def __init__(self, present: list[date], skip: list[date], tickers: list[str],
                 present_after: list[date], *,
                 no_data_already_recorded: set[date] | None = None):
        self._present_calls = 0
        self._present = present
        self._present_after = present_after
        self._skip = skip
        self._tickers = tickers
        # dates for which the existence-check fetchrow should return a truthy row
        self._no_data_already_recorded: set[date] = no_data_already_recorded or set()
        self.executed: list[tuple] = []        # (sql, args)
        self.executemany_calls: list[tuple] = []

    async def fetch(self, sql: str, *args):
        if "DISTINCT" in sql:                  # present dates
            self._present_calls += 1
            src = self._present if self._present_calls == 1 else self._present_after
            return [{"d": d} for d in src]
        if "market_gaps" in sql:               # skip dates
            return [{"session_date": d} for d in self._skip]
        if "companies" in sql:                 # active tickers
            return [{"ticker": t} for t in self._tickers]
        raise AssertionError(f"unexpected fetch: {sql}")

    async def fetchrow(self, sql: str, *args):
        """Existence check in _record_no_data_date: returns a truthy object when
        the date (first positional arg) is in _no_data_already_recorded."""
        if "market_gaps" in sql and args and isinstance(args[0], date):
            if args[0] in self._no_data_already_recorded:
                return {"exists": 1}
        return None

    async def execute(self, sql: str, *args):
        self.executed.append((sql, args))

    async def executemany(self, sql: str, rows):
        self.executemany_calls.append((sql, list(rows)))


def _fake_stream(frames_by_ticker: dict[str, pd.DataFrame], fail: set[str] = frozenset()):
    async def fetch(ticker: str, **kwargs):
        if ticker in fail:
            raise RuntimeError(f"all adapters failed for {ticker}")
        result = MagicMock()
        result.data = frames_by_ticker.get(ticker, pd.DataFrame())
        return result
    stream = MagicMock()
    stream.fetch = AsyncMock(side_effect=fetch)
    return stream


WINDOW = [date(2026, 6, 4), date(2026, 6, 7), date(2026, 6, 8),
          date(2026, 6, 9), date(2026, 6, 10)]


def _bar(ticker: str, d: date) -> dict:
    return {"ticker": ticker, "date": datetime(d.year, d.month, d.day, tzinfo=UTC),
            "close": Decimal("100"), "source": "bdshare_historical"}


def _run(pool, stream, **settings_overrides):
    """Run backfill_missing_dates with all collaborators patched."""
    import asyncio as _asyncio
    cfg = MagicMock()
    cfg.gap_backfill_enabled = settings_overrides.get("enabled", True)
    cfg.gap_backfill_window_days = settings_overrides.get("window_days", 7)

    with patch("extraction.gap_backfill._get_pool", AsyncMock(return_value=pool)), \
         patch("extraction.gap_backfill.get_settings", return_value=cfg), \
         patch("extraction.gap_backfill.fire_alert", AsyncMock()) as alert, \
         patch("extraction.gap_backfill._now_bd_date", return_value=date(2026, 6, 11)), \
         patch.dict("extraction.registry.STREAMS", {"historical_ohlcv": stream}), \
         patch("extraction.gap_backfill._FETCH_DELAY_SECONDS", 0):
        summary = _asyncio.run(backfill_missing_dates())
    return summary, alert


def test_backfill_disabled_short_circuits():
    pool = FakePool([], [], [], [])
    summary, _ = _run(pool, _fake_stream({}), enabled=False)
    assert summary == {"missing": 0, "recovered_dates": 0, "no_data_dates": 0,
                       "rows_inserted": 0, "tickers_failed": 0, "ca_refresh_failed": 0}
    assert pool.executed == [] and pool.executemany_calls == []


def test_backfill_no_missing_dates_no_fetch():
    pool = FakePool(WINDOW, [], ["GP"], WINDOW)
    stream = _fake_stream({})
    summary, alert = _run(pool, stream)
    assert summary["missing"] == 0
    stream.fetch.assert_not_called()
    alert.assert_not_called()


def test_backfill_recovers_missing_date():
    gap = date(2026, 6, 8)
    present = [d for d in WINDOW if d != gap]
    pool = FakePool(present, [], ["GP", "BRACBANK"], WINDOW)
    stream = _fake_stream({"GP": pd.DataFrame([_bar("GP", gap)]),
                           "BRACBANK": pd.DataFrame([_bar("BRACBANK", gap)])})
    summary, alert = _run(pool, stream)

    assert summary["missing"] == 1
    assert summary["recovered_dates"] == 1
    assert summary["no_data_dates"] == 0
    assert summary["rows_inserted"] == 2
    # one executemany per ticker with rows
    assert len(pool.executemany_calls) == 2
    # CA refresh: daily, weekly, monthly, sector
    refresh_calls = [sql for sql, _ in pool.executed if "refresh_continuous_aggregate" in sql]
    assert len(refresh_calls) == 4
    assert any("daily_ohlcv" in sql for sql in refresh_calls)
    alert.assert_called_once()


def test_backfill_holiday_recorded_not_refreshed():
    gap = date(2026, 6, 8)
    present = [d for d in WINDOW if d != gap]
    # fetch returns nothing for the gap date; present-after unchanged
    pool = FakePool(present, [], ["GP"], present)
    stream = _fake_stream({"GP": pd.DataFrame()})
    summary, alert = _run(pool, stream)

    assert summary["no_data_dates"] == 1
    assert summary["recovered_dates"] == 0
    gap_inserts = [(sql, args) for sql, args in pool.executed if "market_gaps" in sql]
    assert len(gap_inserts) == 1
    assert gap_inserts[0][1][0] == gap                 # session_date
    assert NO_DATA_REASON in gap_inserts[0][1]
    refresh_calls = [sql for sql, _ in pool.executed if "refresh_continuous_aggregate" in sql]
    assert refresh_calls == []
    alert.assert_called_once()


def test_backfill_skip_dates_excluded():
    gap = date(2026, 6, 8)
    present = [d for d in WINDOW if d != gap]
    pool = FakePool(present, [gap], ["GP"], present)   # gap already recorded
    stream = _fake_stream({})
    summary, _ = _run(pool, stream)
    assert summary["missing"] == 0
    stream.fetch.assert_not_called()


def test_backfill_ticker_failure_counted_continues():
    gap = date(2026, 6, 8)
    present = [d for d in WINDOW if d != gap]
    pool = FakePool(present, [], ["FAILCO", "GP"], WINDOW)
    stream = _fake_stream({"GP": pd.DataFrame([_bar("GP", gap)])}, fail={"FAILCO"})
    summary, _ = _run(pool, stream)
    assert summary["tickers_failed"] == 1
    assert summary["rows_inserted"] == 1
    assert summary["recovered_dates"] == 1


def test_backfill_never_raises_on_db_error():
    import asyncio as _asyncio
    with patch("extraction.gap_backfill._get_pool", AsyncMock(side_effect=RuntimeError("db down"))), \
         patch("extraction.gap_backfill.get_settings") as gs:
        gs.return_value.gap_backfill_enabled = True
        gs.return_value.gap_backfill_window_days = 7
        summary = _asyncio.run(backfill_missing_dates())
    assert summary["missing"] == 0


def test_backfill_no_data_insert_skipped_when_already_recorded():
    """_record_no_data_date must skip the INSERT when the existence-check fetchrow
    returns a row — prevents duplicate market_gaps entries on overlapping boot +
    cron backfill passes."""
    gap = date(2026, 6, 8)
    present = [d for d in WINDOW if d != gap]
    # fetchrow will return a truthy row for this date (already recorded)
    pool = FakePool(present, [], ["GP"], present,
                    no_data_already_recorded={gap})
    stream = _fake_stream({"GP": pd.DataFrame()})   # still empty after fetch
    summary, alert = _run(pool, stream)

    assert summary["no_data_dates"] == 1
    # No INSERT into market_gaps should have been executed
    gap_inserts = [(sql, args) for sql, args in pool.executed if "market_gaps" in sql]
    assert gap_inserts == [], "INSERT must be skipped when row already recorded"
    alert.assert_called_once()


def test_backfill_ca_refresh_failures_counted():
    """CA refresh failures must be counted in summary and included in the alert
    details; the run must still complete and fire_alert must still be called."""
    gap = date(2026, 6, 8)
    present = [d for d in WINDOW if d != gap]

    class BrokenRefreshPool(FakePool):
        async def execute(self, sql: str, *args):
            if "refresh_continuous_aggregate" in sql:
                raise RuntimeError("CA refresh boom")
            await super().execute(sql, *args)

    pool = BrokenRefreshPool(present, [], ["GP"], WINDOW)
    stream = _fake_stream({"GP": pd.DataFrame([_bar("GP", gap)])})
    summary, alert = _run(pool, stream)

    # All 4 CA views fail
    assert summary["ca_refresh_failed"] == 4
    # Run still completes with a recovered date
    assert summary["recovered_dates"] == 1
    # alert was still fired and includes the count
    alert.assert_called_once()
    call_kwargs = alert.call_args.kwargs
    assert call_kwargs["details"]["ca_refresh_failed"] == 4


def test_rows_from_frame_single_side_mid_decimal_coercion():
    """rows_from_frame must coerce the _mid fallback to Decimal even when only
    one side (high or low) is present and _mid returns the raw float/int."""
    df = _frame([{
        "ticker": "GP", "date": datetime(2026, 6, 8, tzinfo=UTC),
        "low": 308.0,          # float, high absent → _mid returns raw float
        "source": "amarstock_historical",
    }])
    rows = rows_from_frame(df, {date(2026, 6, 8)}, INGESTED)
    assert len(rows) == 1
    assert rows[0][5] == Decimal("308.0")
    assert isinstance(rows[0][5], Decimal)
    assert rows[0][13] == "no_ohlc"
