# Price Gap Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Self-healing daily job that finds trading dates in the last 30 days with zero `stock_prices` rows, refills them from the `historical_ohlcv` adapter chain, and refreshes the continuous aggregates.

**Architecture:** New module `extraction/gap_backfill.py` following the `extraction/recovery.py` pattern — pure decision functions (offline unit tests) + async orchestrator with DB access through a patchable `_get_pool()`. Wired in twice: a daily 18:00 Asia/Dhaka cron job in `extraction/scheduler.py`, and a `CatchUpJob` entry in `extraction/recovery.py`'s `CATCHUP_REGISTRY` so boot recovery catches it up after downtime.

**Tech Stack:** Python 3.12, asyncpg, pandas, APScheduler, TimescaleDB continuous aggregates, pytest + unittest.mock.

**Spec:** `docs/superpowers/specs/2026-06-11-price-gap-backfill-design.md`

**Conventions you must know:**
- DSE trades Sun–Thu. Python `weekday()`: Mon=0..Sun=6, so market weekdays = `{6, 0, 1, 2, 3}`.
- Daily bars are stored in `stock_prices` with `time` = trading date at **00:00 UTC** (matches `daily_ohlcv` bucketing).
- Unique index `(time, ticker)` exists (migration 008) — inserts use `ON CONFLICT DO NOTHING`.
- The `historical_ohlcv` stream (`extraction/registry.py:55`) tries `AmarStockCSVAdapter` first, `BDShareHistoricalAdapter` second. AmarStock bars have **no open/close** — existing convention (`extraction/bulk_load/historical_loader.py`) is `close=(high+low)/2`, `quality_flag='no_ohlc'`.
- `daily_ohlcv` refresh policy has `start_offset = 3 days` (migration 003) — backfilled rows older than that need explicit `CALL refresh_continuous_aggregate`.
- `market_gaps` table (migration 035): `id, session_date, gap_start, gap_end, reason, recovered, detected_at`.
- Run tests with: `pytest tests/unit/test_gap_backfill.py -v --no-cov` (unit tests are offline; DB is mocked).

**File map:**
- Create: `extraction/gap_backfill.py` — detection + orchestrator
- Create: `tests/unit/test_gap_backfill.py` — all unit tests
- Modify: `mgmt/config.py` — 4 new settings
- Modify: `extraction/scheduler.py` — `job_price_gap_backfill()` + `add_job`
- Modify: `extraction/recovery.py` — registry entry
- Modify: `tests/unit/test_recovery.py` — registry coverage test

---

### Task 1: Config settings

**Files:**
- Modify: `mgmt/config.py` (after the `recovery_enabled` / `intraday_gap_threshold_minutes` block, ~line 117)
- Test: `tests/unit/test_gap_backfill.py` (new file)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_gap_backfill.py`:

```python
"""Tests for daily-bar gap backfill: detection, row building, orchestrator."""
from __future__ import annotations

from mgmt.config import get_settings


def test_gap_backfill_settings_defaults():
    cfg = get_settings()
    assert cfg.gap_backfill_enabled is True
    assert cfg.gap_backfill_window_days == 30
    assert cfg.gap_backfill_hour == 18
    assert cfg.gap_backfill_minute == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_gap_backfill.py -v --no-cov`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'gap_backfill_enabled'`

- [ ] **Step 3: Add settings**

In `mgmt/config.py`, directly after the `intraday_gap_threshold_minutes: int = 3` line (inside the "Crash / downtime recovery" section), add:

```python
    # ── Daily-bar gap backfill ────────────────────────────────────────────
    # job_price_gap_backfill scans the last N days for Sun–Thu dates with zero
    # stock_prices rows and refills them from the historical_ohlcv chain.
    gap_backfill_enabled: bool = True
    gap_backfill_window_days: int = 30
    gap_backfill_hour: int = 18      # 18:00 Asia/Dhaka — after EOD + CA refresh chain
    gap_backfill_minute: int = 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_gap_backfill.py -v --no-cov`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mgmt/config.py tests/unit/test_gap_backfill.py
git commit -m "feat(config): gap backfill settings — enabled, window, anchor time"
```

---

### Task 2: `find_missing_dates` (pure detection)

**Files:**
- Create: `extraction/gap_backfill.py`
- Test: `tests/unit/test_gap_backfill.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_gap_backfill.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_gap_backfill.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'extraction.gap_backfill'`

- [ ] **Step 3: Create module with the pure function**

Create `extraction/gap_backfill.py`:

```python
"""Daily-bar gap backfill — self-healing for whole missed trading days.

job_price_gap_backfill (daily 18:00 BD cron; also caught up by boot recovery)
scans the last gap_backfill_window_days for Sun–Thu dates with zero
stock_prices rows, refills them per ticker from the historical_ohlcv chain,
and refreshes the continuous aggregates so backfilled bars appear in the
daily/weekly/monthly/sector views (daily_ohlcv's refresh policy only looks
back 3 days, so old inserts never materialize on their own).

Dates that stay empty after a fetch pass (DSE holidays) are recorded in
market_gaps with reason='backfill_no_data' and excluded from future scans.

Pure decision logic lives in find_missing_dates(); DB access goes through
_get_pool() so tests can patch it.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg
    import pandas as pd

import pytz

from extraction.observability import fire_alert
from mgmt.config import get_settings

logger = logging.getLogger(__name__)
BD_TZ = pytz.timezone("Asia/Dhaka")

# Python weekday(): Mon=0..Sun=6. DSE trades Sun–Thu → {6,0,1,2,3}.
_MARKET_WEEKDAYS = {6, 0, 1, 2, 3}

NO_DATA_REASON = "backfill_no_data"
_FETCH_CONCURRENCY = 3
_FETCH_DELAY_SECONDS = 1.5


async def _get_pool() -> asyncpg.Pool:
    """Indirection so tests can patch the DB pool without a live database."""
    from db.pool import get_pool
    return await get_pool()


def find_missing_dates(
    present: set[date],
    skip: set[date],
    today: date,
    window_days: int,
) -> list[date]:
    """Trading dates (Sun–Thu) in [today - window_days, today - 1] that are in
    neither `present` (have stock_prices rows) nor `skip` (recorded as
    holiday/no-data). Today is excluded — live/EOD jobs own today's bar.
    """
    out: list[date] = []
    d = today - timedelta(days=window_days)
    while d < today:
        if d.weekday() in _MARKET_WEEKDAYS and d not in present and d not in skip:
            out.append(d)
        d += timedelta(days=1)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_gap_backfill.py -v --no-cov`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add extraction/gap_backfill.py tests/unit/test_gap_backfill.py
git commit -m "feat(backfill): find_missing_dates — pure 30-day trading-date gap detection"
```

---

### Task 3: `rows_from_frame` (DataFrame → insert tuples)

**Files:**
- Modify: `extraction/gap_backfill.py`
- Test: `tests/unit/test_gap_backfill.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_gap_backfill.py`:

```python
from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd

from extraction.gap_backfill import rows_from_frame

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_gap_backfill.py -v --no-cov`
Expected: FAIL with `ImportError: cannot import name 'rows_from_frame'`

- [ ] **Step 3: Implement**

In `extraction/gap_backfill.py`:

Change the pandas import — it is needed at runtime now. Replace the `if TYPE_CHECKING:` block with:

```python
import pandas as pd

if TYPE_CHECKING:
    import asyncpg
```

Add after `find_missing_dates`:

```python
# Same column set as bulk_load loaders. DO NOTHING — backfill must never
# clobber bars written by the live/EOD jobs or earlier loads.
_INSERT_SQL = """
    INSERT INTO stock_prices
        (time, ticker, open, high, low, close,
         volume, trades, value_bdt, prev_close, change_pct,
         source, ingested_at, quality_flag)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
    ON CONFLICT (time, ticker) DO NOTHING
"""


def _opt_int(val: object) -> int | None:
    if val is None or pd.isna(val):
        return None
    return int(val)


def rows_from_frame(
    df: pd.DataFrame,
    missing: set[date],
    ingested_at: datetime,
) -> list[tuple]:
    """_INSERT_SQL tuples for bars whose trading date is in `missing`.

    Bars without a close (amarstock_historical carries only high/low) get
    close=(high+low)/2 and quality_flag='no_ohlc' — the bulk-loader
    convention; a later bdshare pass can upgrade them to real OHLCV.
    """
    from extraction.bulk_load.historical_loader import _mid  # noqa: PLC0415

    rows: list[tuple] = []
    if df.empty:
        return rows
    for _, row in df.iterrows():
        ts = row.get("date")
        if ts is None or pd.isna(ts):
            continue
        ts = pd.Timestamp(ts).to_pydatetime()
        if ts.date() not in missing:
            continue
        close = row.get("close")
        quality = "ok"
        if close is None:
            close = _mid(row.get("high"), row.get("low"))
            quality = "no_ohlc"
        if close is None:
            continue
        rows.append((
            ts,
            row["ticker"],
            row.get("open"),
            row.get("high"),
            row.get("low"),
            close,
            _opt_int(row.get("volume")),
            _opt_int(row.get("trades")),
            row.get("value_bdt"),
            None,   # prev_close
            None,   # change_pct
            row.get("source"),
            ingested_at,
            quality,
        ))
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_gap_backfill.py -v --no-cov`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add extraction/gap_backfill.py tests/unit/test_gap_backfill.py
git commit -m "feat(backfill): rows_from_frame — adapter frame to stock_prices tuples"
```

---

### Task 4: `backfill_missing_dates` orchestrator

**Files:**
- Modify: `extraction/gap_backfill.py`
- Test: `tests/unit/test_gap_backfill.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_gap_backfill.py`. Mock strategy: a fake pool whose `fetch` answers by SQL keyword (robust to call order), a fake stream injected into `extraction.registry.STREAMS`, and `fire_alert` patched where `gap_backfill` imported it.

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from extraction.gap_backfill import NO_DATA_REASON, backfill_missing_dates


class FakePool:
    """Answers pool.fetch by SQL keyword; records execute/executemany calls."""

    def __init__(self, present: list[date], skip: list[date], tickers: list[str],
                 present_after: list[date]):
        self._present_calls = 0
        self._present = present
        self._present_after = present_after
        self._skip = skip
        self._tickers = tickers
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
    cfg = MagicMock()
    cfg.gap_backfill_enabled = settings_overrides.get("enabled", True)
    cfg.gap_backfill_window_days = settings_overrides.get("window_days", 7)
    fixed_now = datetime(2026, 6, 11, 18, 0)

    import asyncio as _asyncio
    with patch("extraction.gap_backfill._get_pool", AsyncMock(return_value=pool)), \
         patch("extraction.gap_backfill.get_settings", return_value=cfg), \
         patch("extraction.gap_backfill.fire_alert", AsyncMock()) as alert, \
         patch("extraction.gap_backfill._now_bd_date", return_value=fixed_now.date()), \
         patch.dict("extraction.registry.STREAMS", {"historical_ohlcv": stream}), \
         patch("extraction.gap_backfill._FETCH_DELAY_SECONDS", 0):
        summary = _asyncio.run(backfill_missing_dates())
    return summary, alert


def test_backfill_disabled_short_circuits():
    pool = FakePool([], [], [], [])
    summary, _ = _run(pool, _fake_stream({}), enabled=False)
    assert summary == {"missing": 0, "recovered_dates": 0, "no_data_dates": 0,
                       "rows_inserted": 0, "tickers_failed": 0}
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
    with patch("extraction.gap_backfill._get_pool", AsyncMock(side_effect=RuntimeError("db down"))), \
         patch("extraction.gap_backfill.get_settings") as gs:
        gs.return_value.gap_backfill_enabled = True
        gs.return_value.gap_backfill_window_days = 7
        import asyncio as _asyncio
        summary = _asyncio.run(backfill_missing_dates())
    assert summary["missing"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_gap_backfill.py -v --no-cov`
Expected: FAIL with `ImportError: cannot import name 'backfill_missing_dates'`

- [ ] **Step 3: Implement the orchestrator**

Append to `extraction/gap_backfill.py`:

```python
# Refresh order matters: weekly/monthly are hierarchical CAs on daily_ohlcv,
# and sector_daily_stats joins daily_ohlcv. Daily must materialize first.
_CA_VIEWS = ("daily_ohlcv", "weekly_ohlcv", "monthly_ohlcv", "sector_daily_stats")


def _now_bd_date() -> date:
    """Current BD calendar date. Wrapped so tests can patch it."""
    return datetime.now(BD_TZ).date()


def _day_start_utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


async def _present_dates(pool: asyncpg.Pool, window_start: date) -> set[date]:
    """Trading dates that already have at least one stock_prices row."""
    rows = await pool.fetch(
        "SELECT DISTINCT (time AT TIME ZONE 'UTC')::date AS d "
        "FROM stock_prices WHERE time >= $1",
        _day_start_utc(window_start),
    )
    return {r["d"] for r in rows}


async def _skip_dates(pool: asyncpg.Pool) -> set[date]:
    """Dates already recorded as holiday/no-data by a previous backfill pass."""
    rows = await pool.fetch(
        "SELECT session_date FROM market_gaps WHERE reason = $1",
        NO_DATA_REASON,
    )
    return {r["session_date"] for r in rows}


async def _fetch_ticker_rows(
    ticker: str,
    start: str,
    end: str,
    missing: set[date],
    semaphore: asyncio.Semaphore,
) -> list[tuple]:
    """One ticker through the historical_ohlcv chain, filtered to missing dates.
    Raises on AllAdaptersFailedError — caller counts and continues."""
    from extraction.registry import STREAMS  # noqa: PLC0415 — heavy import deferred

    async with semaphore:
        try:
            result = await STREAMS["historical_ohlcv"].fetch(
                ticker=ticker, start=start, end=end
            )
        finally:
            await asyncio.sleep(_FETCH_DELAY_SECONDS)
    return rows_from_frame(result.data, missing, datetime.now(UTC))


async def _refresh_aggregates(pool: asyncpg.Pool, lo: date, hi: date) -> None:
    """Materialize backfilled rows into the CA chain. daily_ohlcv's policy has
    start_offset='3 days', so older inserts never refresh on their own.
    Per-view failures are logged, not raised — data is safe in stock_prices."""
    lo_ts = _day_start_utc(lo)
    hi_ts = _day_start_utc(hi) + timedelta(days=1)
    for view in _CA_VIEWS:
        try:
            await pool.execute(
                f"CALL refresh_continuous_aggregate('{view}', $1, $2)", lo_ts, hi_ts
            )
        except Exception as exc:
            logger.warning("gap_backfill: CA refresh failed view=%s error=%s", view, exc)


async def backfill_missing_dates() -> dict[str, int]:
    """Scan, refill, refresh. Returns a summary dict; never raises — this runs
    inside scheduler jobs and boot recovery, neither may crash.

    rows_inserted counts rows sent to the DO NOTHING upsert, not rows that
    actually landed (a partial day would dedupe silently — acceptable)."""
    summary = {"missing": 0, "recovered_dates": 0, "no_data_dates": 0,
               "rows_inserted": 0, "tickers_failed": 0}
    cfg = get_settings()
    if not cfg.gap_backfill_enabled:
        logger.info("gap_backfill: disabled (gap_backfill_enabled=False)")
        return summary

    try:
        pool = await _get_pool()
        today = _now_bd_date()
        window_start = today - timedelta(days=cfg.gap_backfill_window_days)
        present = await _present_dates(pool, window_start)
        skip = await _skip_dates(pool)
        missing = find_missing_dates(present, skip, today, cfg.gap_backfill_window_days)
        summary["missing"] = len(missing)
        if not missing:
            logger.info("gap_backfill: no missing dates in last %d days",
                        cfg.gap_backfill_window_days)
            return summary

        logger.warning("gap_backfill: missing dates: %s",
                       ", ".join(d.isoformat() for d in missing))
        missing_set = set(missing)
        start, end = missing[0].isoformat(), missing[-1].isoformat()

        tickers = [r["ticker"] for r in await pool.fetch(
            "SELECT ticker FROM companies WHERE is_active = true ORDER BY ticker"
        )]
        semaphore = asyncio.Semaphore(_FETCH_CONCURRENCY)

        async def _one(ticker: str) -> int:
            try:
                rows = await _fetch_ticker_rows(ticker, start, end, missing_set, semaphore)
            except Exception as exc:
                summary["tickers_failed"] += 1
                logger.warning("gap_backfill: ticker failed ticker=%s error=%s",
                               ticker, exc)
                return 0
            if rows:
                await pool.executemany(_INSERT_SQL, rows)
            return len(rows)

        counts = await asyncio.gather(*(_one(t) for t in tickers))
        summary["rows_inserted"] = sum(counts)

        # Which dates actually filled? Still-empty = holiday/no-data → record
        # so the next scan skips them. Recovered → refresh the CA chain.
        after = await _present_dates(pool, window_start)
        recovered = [d for d in missing if d in after]
        still_empty = [d for d in missing if d not in after]
        summary["recovered_dates"] = len(recovered)
        summary["no_data_dates"] = len(still_empty)

        for d in still_empty:
            await pool.execute(
                "INSERT INTO market_gaps (session_date, gap_start, gap_end, reason, recovered) "
                "VALUES ($1, $2, $3, $4, TRUE)",
                d, _day_start_utc(d), _day_start_utc(d) + timedelta(days=1),
                NO_DATA_REASON,
            )

        if recovered:
            await _refresh_aggregates(pool, recovered[0], recovered[-1])

        await fire_alert(
            severity="WARNING",
            message=(f"Price gap backfill: {len(missing)} missing date(s), "
                     f"recovered {len(recovered)}, no-data {len(still_empty)}"),
            stream_name="historical_ohlcv",
            details={
                "missing": [d.isoformat() for d in missing],
                "recovered": [d.isoformat() for d in recovered],
                "no_data": [d.isoformat() for d in still_empty],
                "rows_inserted": summary["rows_inserted"],
                "tickers_failed": summary["tickers_failed"],
            },
        )
    except Exception as exc:
        logger.error("gap_backfill: aborted error=%s", exc, exc_info=True)

    logger.info(
        "gap_backfill_summary missing=%d recovered=%d no_data=%d rows=%d failed_tickers=%d",
        summary["missing"], summary["recovered_dates"], summary["no_data_dates"],
        summary["rows_inserted"], summary["tickers_failed"],
    )
    return summary
```

Note for the engineer: `patch("extraction.gap_backfill._FETCH_DELAY_SECONDS", 0)` in the test patches the module attribute, but `_fetch_ticker_rows` reads it at call time via module global lookup — this works because the function references the name, not a captured value.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_gap_backfill.py -v --no-cov`
Expected: all PASS. If `test_backfill_recovers_missing_date` fails on `rows_inserted`, check that the fake stream's frame dates fall inside `missing_set` (00:00 UTC convention).

- [ ] **Step 5: Run lint + typecheck on the new module**

Run: `ruff check extraction/gap_backfill.py tests/unit/test_gap_backfill.py && mypy extraction/gap_backfill.py`
Expected: clean. Fix any findings (unused imports, line length) before committing.

- [ ] **Step 6: Commit**

```bash
git add extraction/gap_backfill.py tests/unit/test_gap_backfill.py
git commit -m "feat(backfill): backfill_missing_dates orchestrator — fetch, insert, CA refresh, holiday ledger"
```

---

### Task 5: Scheduler job + cron wiring

**Files:**
- Modify: `extraction/scheduler.py` — new job function near `job_eod_snapshot` (~line 433), new `add_job` block in the scheduler-start section (after the `eod_snapshot` add_job, ~line 865)

- [ ] **Step 1: Add the job function**

In `extraction/scheduler.py`, directly after `job_eod_snapshot` (after its closing `logger.info("job_eod_snapshot: complete")` line), add:

```python
async def job_price_gap_backfill() -> None:
    """Scan the last N days for missing daily bars and refill them
    (see extraction/gap_backfill.py)."""
    from extraction.gap_backfill import backfill_missing_dates
    from extraction.jobs import job_run
    logger.info("job_price_gap_backfill: starting")
    async with job_run("price_gap_backfill") as ctx:
        summary = await backfill_missing_dates()
        ctx.update(summary)
    logger.info("job_price_gap_backfill: complete %s", summary)
```

- [ ] **Step 2: Register the cron job**

In the scheduler-start section, directly after the `eod_snapshot` `add_job` block (~line 865), add:

```python
    scheduler.add_job(
        job_price_gap_backfill,
        trigger="cron",
        hour=cfg.gap_backfill_hour,
        minute=cfg.gap_backfill_minute,
        id="price_gap_backfill",
        replace_existing=True,
        misfire_grace_time=3600,
    )
```

Note: no `day_of_week` — runs every day. Scans are one cheap query; Fri/Sat runs find nothing new but catch any date the previous run missed.

- [ ] **Step 3: Verify imports + no syntax errors**

Run: `python -c "import extraction.scheduler"`
Expected: imports cleanly (module-level import must not pull a DB connection).

Run: `ruff check extraction/scheduler.py`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add extraction/scheduler.py
git commit -m "feat(scheduler): job_price_gap_backfill — daily 18:00 BD gap scan + refill"
```

---

### Task 6: CATCHUP_REGISTRY entry

**Files:**
- Modify: `extraction/recovery.py:128-165` (`_build_registry`)
- Test: `tests/unit/test_recovery.py:104-109` (`test_registry_covers_expected_jobs`)

- [ ] **Step 1: Update the failing test first**

In `tests/unit/test_recovery.py`, replace `test_registry_covers_expected_jobs` with:

```python
def test_registry_covers_expected_jobs():
    names = {j.job_name for j in CATCHUP_REGISTRY}
    assert names == {
        "daily_macro", "news_sentiment", "seed_companies", "eod_snapshot",
        "sector_pe", "nightly_ml", "weekly_fundamentals", "monthly", "quarterly",
        "price_gap_backfill",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_recovery.py::test_registry_covers_expected_jobs -v --no-cov`
Expected: FAIL — `price_gap_backfill` not in registry.

- [ ] **Step 3: Add the registry entry**

In `extraction/recovery.py` `_build_registry()`:

Add `job_price_gap_backfill` to the import from `extraction.scheduler`:

```python
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
```

Add the entry to the returned list, after the `sector_pe` entry (dep_rank 3 — runs after eod_snapshot rank 2; job_name must match the `job_run("price_gap_backfill")` name):

```python
        CatchUpJob("price_gap_backfill", job_price_gap_backfill,
                   cfg.gap_backfill_hour, cfg.gap_backfill_minute, _is_every_day, 3),
```

- [ ] **Step 4: Run recovery tests to verify they pass**

Run: `pytest tests/unit/test_recovery.py -v --no-cov`
Expected: all PASS (including `test_registry_job_names_unique`).

- [ ] **Step 5: Commit**

```bash
git add extraction/recovery.py tests/unit/test_recovery.py
git commit -m "feat(recovery): price_gap_backfill in CATCHUP_REGISTRY — boot catch-up after downtime"
```

---

### Task 7: Full verification

- [ ] **Step 1: Full unit suite**

Run: `make test`
Expected: all pass, no regressions.

- [ ] **Step 2: Lint + typecheck everything touched**

Run: `make check`
Expected: clean.

- [ ] **Step 3: Boot-path sanity**

Run: `python -c "from extraction.recovery import CATCHUP_REGISTRY; print([j.job_name for j in CATCHUP_REGISTRY])"`
Expected: list includes `price_gap_backfill`, no import errors.

- [ ] **Step 4: Commit any fixes**

```bash
git add -A
git commit -m "chore(backfill): verification fixes"
```

(Skip commit if nothing changed.)
