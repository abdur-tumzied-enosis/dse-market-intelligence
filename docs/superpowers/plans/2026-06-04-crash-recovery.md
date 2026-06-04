# Crash / Downtime Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On scheduler restart, converge the pipeline to the state it should be in — catch up missed daily/EOD jobs and make live-price gaps visible.

**Architecture:** A boot-time reconciler (`extraction/recovery.py`) queries the `pipeline_jobs` ledger to decide which anchored jobs are overdue-and-not-done today, then runs them once in dependency order. A `market_gaps` ledger records live-price polling holes, detected per-poll inside `job_live_prices` and on boot. Pure decision functions (`should_catch_up`, `detect_gap`) are unit-tested offline; orchestration is tested with a mocked pool.

**Tech Stack:** Python 3.11+ async, APScheduler, asyncpg, PostgreSQL/TimescaleDB, pytest. Spec: `docs/superpowers/specs/2026-06-04-crash-recovery-design.md`.

---

## File Structure

| File | Responsibility |
|---|---|
| `db/migrations/035_market_gaps.sql` | **new** — `market_gaps` ledger table |
| `extraction/recovery.py` | **new** — pure predicates, catch-up registry, reconciler, gap recording |
| `extraction/scheduler.py` | **modify** — call reconciler on boot; per-poll gap check in `job_live_prices`; wrap 6 jobs in `job_run` |
| `mgmt/config.py` | **modify** — `recovery_enabled`, `intraday_gap_threshold_minutes` |
| `tests/unit/test_recovery.py` | **new** — pure-function + orchestration (mocked pool) tests |

Conventions to follow (from existing code):
- `BD_TZ = pytz.timezone("Asia/Dhaka")` (as in `scheduler.py:26`). Python weekday: Mon=0..Sun=6; DSE market days = `{6,0,1,2,3}` (Sun–Thu), Fri=4/Sat=5 closed (`market_status.py:41`).
- DB access via a module-level `_get_pool()` indirection so tests can patch it (pattern from `market_status.py:47`).
- `fire_alert(severity, message, stream_name=None, details=None)` — `extraction/observability.py:107`.
- `get_market_status()` returns a dict with `["status"]` == `"Open"`/`"Closed"` (`market_status.py:120`).
- `job_run(job_name, stream_name=None)` async context manager records a `pipeline_jobs` row (`extraction/jobs.py:17`).

---

## Task 1: Migration — `market_gaps` table

**Files:**
- Create: `db/migrations/035_market_gaps.sql`

- [ ] **Step 1: Write the migration SQL**

Create `db/migrations/035_market_gaps.sql`:

```sql
-- market_gaps — durable record of live-price polling holes during market hours.
-- Volume/value self-recover (session-cumulative feed); only intraday PRICE
-- granularity is lost across a gap. This ledger makes holes visible so consumers
-- can flag affected bars, and gives a backfill target list (recovered flag).

CREATE TABLE IF NOT EXISTS market_gaps (
    id           BIGSERIAL PRIMARY KEY,
    session_date DATE        NOT NULL,
    gap_start    TIMESTAMPTZ NOT NULL,   -- last snapshot instant before the gap (UTC)
    gap_end      TIMESTAMPTZ NOT NULL,   -- first snapshot instant after the gap (UTC)
    reason       TEXT        NOT NULL DEFAULT 'scheduler_downtime',
    recovered    BOOLEAN     NOT NULL DEFAULT FALSE,
    detected_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_market_gaps_session_date
    ON market_gaps (session_date);
CREATE INDEX IF NOT EXISTS idx_market_gaps_unrecovered
    ON market_gaps (recovered) WHERE recovered = FALSE;
```

- [ ] **Step 2: Apply the migration**

Run: `make migrate-local`
Expected: output lists `035_market_gaps.sql` as applied; no error. Re-running is a no-op (`IF NOT EXISTS`).

- [ ] **Step 3: Verify the table exists**

Run: `make db-shell` then `\d market_gaps`
Expected: table with columns `id, session_date, gap_start, gap_end, reason, recovered, detected_at` and the two indexes.

- [ ] **Step 4: Commit**

```bash
git add db/migrations/035_market_gaps.sql
git commit -m "feat(db): add market_gaps ledger (migration 035)"
```

---

## Task 2: Config — recovery settings

**Files:**
- Modify: `mgmt/config.py` (after the health_checks block, ~line 105)
- Test: `tests/unit/test_recovery.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_recovery.py` with:

```python
"""Tests for crash/downtime recovery: reconciler + gap detection."""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytz

from mgmt.config import get_settings

BD_TZ = pytz.timezone("Asia/Dhaka")


def test_recovery_settings_defaults():
    cfg = get_settings()
    assert cfg.recovery_enabled is True
    assert cfg.intraday_gap_threshold_minutes == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_recovery.py::test_recovery_settings_defaults -v`
Expected: FAIL with `AttributeError` (no `recovery_enabled`).

- [ ] **Step 3: Add the settings**

In `mgmt/config.py`, after the health_checks line (`health_check_interval_hours: int = 6`, ~line 105), add:

```python
    # ── Crash / downtime recovery ─────────────────────────────────────────
    # On scheduler boot, recover_missed_jobs() catches up anchored daily/EOD
    # jobs that were due today but never ran (host was down through their time).
    recovery_enabled: bool = True
    # Minutes without an intraday_prices row (during market hours) before a
    # market_gaps row is recorded. One missed */1 poll of slack.
    intraday_gap_threshold_minutes: int = 3
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_recovery.py::test_recovery_settings_defaults -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mgmt/config.py tests/unit/test_recovery.py
git commit -m "feat(config): add recovery_enabled + intraday_gap_threshold_minutes"
```

---

## Task 3: Pure decision functions — `should_catch_up`, `detect_gap`

**Files:**
- Create: `extraction/recovery.py`
- Test: `tests/unit/test_recovery.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_recovery.py`:

```python
from extraction.recovery import detect_gap, should_catch_up


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_recovery.py -k "should_catch_up or detect_gap" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'extraction.recovery'`.

- [ ] **Step 3: Create the module with the pure functions**

Create `extraction/recovery.py`:

```python
"""Crash / downtime recovery for the extraction scheduler.

On boot, recover_missed_jobs() converges the pipeline to the state it should be
in: anchored daily/EOD jobs that were due earlier today but never ran are run
once (catch-up), and live-price polling gaps are recorded to market_gaps.

Pure decision functions (should_catch_up, detect_gap) carry the logic and are
unit-tested offline; DB access goes through _get_pool() so it can be patched.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

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
    if now_utc - last_snapshot_time > timedelta(minutes=threshold_minutes):
        return (last_snapshot_time, now_utc)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_recovery.py -k "should_catch_up or detect_gap" -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add extraction/recovery.py tests/unit/test_recovery.py
git commit -m "feat(recovery): pure decision functions should_catch_up + detect_gap"
```

---

## Task 4: Catch-up registry + run-day predicates

**Files:**
- Modify: `extraction/recovery.py`
- Test: `tests/unit/test_recovery.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_recovery.py`:

```python
from extraction.recovery import CATCHUP_REGISTRY


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_recovery.py -k registry -v`
Expected: FAIL — `ImportError: cannot import name 'CATCHUP_REGISTRY'`.

- [ ] **Step 3: Add the dataclass, predicates, and registry**

In `extraction/recovery.py`, add after the imports (below `BD_TZ`):

```python
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

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
```

Then add a function that builds the registry (lazy imports avoid a circular
import — `scheduler` imports `recovery`). Add at the **end** of the module:

```python
def _build_registry() -> list[CatchUpJob]:
    from mgmt.config import get_settings
    from extraction.scheduler import (
        job_daily_macro, job_eod_snapshot, job_monthly, job_news_sentiment,
        job_nightly_ml, job_quarterly, job_sector_pe, job_seed_companies,
        job_weekly_fundamentals,
    )
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
```

Note: `_build_registry()` runs at import time. Because `recovery` is imported
lazily from inside `start_scheduler()` (not at `scheduler` module top-level — see
Task 8), the `from extraction.scheduler import ...` here resolves cleanly.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_recovery.py -k registry -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the whole recovery test file**

Run: `pytest tests/unit/test_recovery.py -v`
Expected: PASS (all so far).

- [ ] **Step 6: Commit**

```bash
git add extraction/recovery.py tests/unit/test_recovery.py
git commit -m "feat(recovery): catch-up registry + run-day predicates"
```

---

## Task 5: Gap recording — `_record_gap`, `maybe_record_intraday_gap`

**Files:**
- Modify: `extraction/recovery.py`
- Test: `tests/unit/test_recovery.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_recovery.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest


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
    # one INSERT into market_gaps
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
```

Add the import at the top of the import-from-recovery block in the test file:

```python
from extraction.recovery import maybe_record_intraday_gap
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_recovery.py -k maybe_record -v`
Expected: FAIL — `ImportError` / `AttributeError` for `maybe_record_intraday_gap`.

- [ ] **Step 3: Implement the gap recording helpers**

In `extraction/recovery.py`, add the `_get_pool` indirection near the top (after
`BD_TZ`) and import `get_market_status` + `fire_alert` lazily-but-module-level so
tests can patch them. Add these module-level names:

```python
from extraction.market_status import get_market_status
from extraction.observability import fire_alert


async def _get_pool():
    """Indirection so tests can patch the DB pool without a live database."""
    from db.pool import get_pool
    return await get_pool()
```

Then add the helpers at the end of the module (after `CATCHUP_REGISTRY` is fine;
order does not matter for functions):

```python
def _today_utc_start(now_bd: datetime) -> datetime:
    """00:00 UTC of the current Dhaka date — lower bound for today's intraday rows."""
    d = now_bd.date()
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


async def _record_gap(
    pool, session_date: date, gap_start: datetime, gap_end: datetime, reason: str
) -> bool:
    """Insert a market_gaps row unless an unrecovered row with the same
    (session_date, gap_start) already exists. Returns True if a row was inserted."""
    existing = await pool.fetchrow(
        "SELECT 1 FROM market_gaps "
        "WHERE session_date = $1 AND gap_start = $2 AND recovered = FALSE",
        session_date, gap_start,
    )
    if existing:
        return False
    await pool.execute(
        "INSERT INTO market_gaps (session_date, gap_start, gap_end, reason) "
        "VALUES ($1, $2, $3, $4)",
        session_date, gap_start, gap_end, reason,
    )
    return True


async def maybe_record_intraday_gap(now_bd: datetime) -> bool:
    """If the market is open and intraday_prices has no recent row, record a
    market_gaps entry and fire a WARNING alert. Returns True if a gap was
    recorded. Never raises — recovery must not crash the caller.
    """
    try:
        if (await get_market_status())["status"] != "Open":
            return False
        pool = await _get_pool()
        from mgmt.config import get_settings
        threshold = get_settings().intraday_gap_threshold_minutes

        row = await pool.fetchrow(
            "SELECT MAX(time) AS t FROM intraday_prices WHERE time >= $1",
            _today_utc_start(now_bd),
        )
        last_t = row["t"] if row else None
        now_utc = now_bd.astimezone(UTC)
        gap = detect_gap(last_t, now_utc, threshold, True)
        if gap is None:
            return False

        recorded = await _record_gap(
            pool, now_bd.date(), gap[0], gap[1], "scheduler_downtime"
        )
        if recorded:
            logger.warning("recovery: intraday gap %s..%s", gap[0], gap[1])
            await fire_alert(
                severity="WARNING",
                message=f"Intraday polling gap detected: {gap[0]} .. {gap[1]}",
                stream_name="live_prices",
                details={"gap_start": gap[0].isoformat(), "gap_end": gap[1].isoformat()},
            )
        return recorded
    except Exception as exc:
        logger.warning("recovery: gap check failed error=%s", exc)
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_recovery.py -k maybe_record -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add extraction/recovery.py tests/unit/test_recovery.py
git commit -m "feat(recovery): market_gaps recording + maybe_record_intraday_gap"
```

---

## Task 6: Reconciler orchestration — `recover_missed_jobs`

**Files:**
- Modify: `extraction/recovery.py`
- Test: `tests/unit/test_recovery.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_recovery.py`:

```python
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
```

Add to the recovery import line in the test file:

```python
from extraction.recovery import recover_missed_jobs
```

And ensure `get_settings` is patchable — it is imported inside the function, so
patch target is `extraction.recovery.get_settings` only after Step 3 adds a
module-level import. (Step 3 adds it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_recovery.py -k recover -v`
Expected: FAIL — `ImportError` for `recover_missed_jobs` / `_now_bd` / `_maybe_resume_live_prices`.

- [ ] **Step 3: Implement the orchestration**

In `extraction/recovery.py`, add a module-level `get_settings` import and the
orchestration functions. Add near the other top imports:

```python
from mgmt.config import get_settings
```

Add these functions at the end of the module:

```python
def _now_bd() -> datetime:
    """Current time in BD tz. Wrapped so tests can patch it."""
    return datetime.now(BD_TZ)


async def _last_success_date(pool, job_name: str) -> date | None:
    """BD date of the job's most recent successful pipeline_jobs run, or None."""
    row = await pool.fetchrow(
        "SELECT MAX((started_at AT TIME ZONE 'Asia/Dhaka')::date) AS d "
        "FROM pipeline_jobs WHERE job_name = $1 AND status = 'success'",
        job_name,
    )
    return row["d"] if row and row["d"] else None


async def _maybe_resume_live_prices(now_bd: datetime) -> None:
    """If the market is open, fire one live pull immediately so polling resumes
    without waiting for the next cron tick (also mitigates a lost open-trigger).
    The pull's own per-poll gap check records any market_gaps row."""
    try:
        if (await get_market_status())["status"] != "Open":
            return
        from extraction.scheduler import job_live_prices
        logger.info("recovery: market open on boot — triggering live_prices")
        await job_live_prices()
    except Exception as exc:
        logger.warning("recovery: live resume failed error=%s", exc)


async def recover_missed_jobs() -> dict[str, int]:
    """Boot-time reconciler. For each anchored job that was due earlier today on a
    valid run-day but has no success recorded today, run it once in dep_rank order.
    Then resume live polling if the market is open. Never raises."""
    summary = {"checked": 0, "ran": 0, "skipped": 0, "failed": 0}
    cfg = get_settings()
    if not cfg.recovery_enabled:
        logger.info("recovery: disabled (recovery_enabled=False)")
        return summary

    now = _now_bd()
    pool = await _get_pool()

    for job in sorted(CATCHUP_REGISTRY, key=lambda j: j.dep_rank):
        summary["checked"] += 1
        last = await _last_success_date(pool, job.job_name)
        if not should_catch_up(now, job.anchor_hour, job.anchor_minute,
                               job.run_day(now), last):
            summary["skipped"] += 1
            continue
        logger.info("recovery: catching up job=%s (last_success=%s)", job.job_name, last)
        try:
            await job.func()
            summary["ran"] += 1
        except Exception as exc:
            summary["failed"] += 1
            logger.error("recovery: catch-up failed job=%s error=%s", job.job_name, exc)

    await _maybe_resume_live_prices(now)
    logger.info(
        "recovery_summary checked=%d ran=%d skipped=%d failed=%d",
        summary["checked"], summary["ran"], summary["skipped"], summary["failed"],
    )
    return summary
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_recovery.py -k recover -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the whole file**

Run: `pytest tests/unit/test_recovery.py -v`
Expected: PASS (all ~19 tests).

- [ ] **Step 6: Commit**

```bash
git add extraction/recovery.py tests/unit/test_recovery.py
git commit -m "feat(recovery): recover_missed_jobs reconciler orchestration"
```

---

## Task 7: Wrap 6 jobs in `job_run` for ledger coverage

The reconciler's `_last_success_date` only sees jobs that write a `pipeline_jobs`
row. These 6 don't yet. Wrap each body in `async with job_run(<name>) as ctx:`
with the exact `job_name` the registry uses.

**Files:**
- Modify: `extraction/scheduler.py` (`job_seed_companies`, `job_eod_snapshot`, `job_nightly_ml`, `job_weekly_fundamentals`, `job_monthly`, `job_quarterly`)

- [ ] **Step 1: Wrap `job_eod_snapshot`**

In `extraction/scheduler.py`, replace the body of `job_eod_snapshot` (currently
`scheduler.py:414-423`) with:

```python
async def job_eod_snapshot() -> None:
    """End-of-day snapshot and calculations."""
    from extraction.jobs import job_run
    logger.info("job_eod_snapshot: starting")
    async with job_run("eod_snapshot"):
        await _persist_index_snapshot()
        # TODO: implement ingest_eod_snapshot()
        # TODO: update_52week_ranges()
        # TODO: update_circuit_breakers()
        # TODO: update_market_pe()
        # TODO: enqueue celery task: run_ml_inference
    logger.info("job_eod_snapshot: complete")
```

- [ ] **Step 2: Wrap `job_seed_companies`**

Wrap the existing body (`scheduler.py:728-752`). Keep all current logic; add the
`job_run` import and context manager:

```python
async def job_seed_companies() -> None:
    """Refresh the companies roster from DSE + enrich new tickers (daily, pre-open).

    Sources the ticker roster from dsebd.org/company_listing.php so newly listed
    companies get registered (as placeholders), then fills name/sector/category/
    market_cap for every unenriched row from displayCompany.php. Runs before the
    10:00 market open: without a seeded companies row, job_live_prices drops a
    new listing's prices (stock_prices.ticker is FK → companies)."""
    from extraction.bulk_load.enrich_companies import enrich_companies
    from extraction.bulk_load.seed_companies import seed
    from extraction.jobs import job_run
    from mgmt.config import get_settings

    cfg = get_settings()
    sync_url = (
        cfg.database_url
        .replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgresql+psycopg://", "postgresql://")
    )
    logger.info("job_seed_companies: starting")
    async with job_run("seed_companies") as ctx:
        roster = await seed(sync_url)
        enriched = await enrich_companies()
        ctx["records_inserted"] = roster["new"]
    logger.info(
        "job_seed_companies: complete roster_new=%d roster_total=%d enriched_ok=%d enriched_failed=%d",
        roster["new"], roster["total"], enriched["ok"], enriched["failed"],
    )
```

- [ ] **Step 3: Wrap `job_nightly_ml` and `job_quarterly`**

Replace `job_nightly_ml` (`scheduler.py:619-628`):

```python
async def job_nightly_ml() -> None:
    """
    Nightly ML inference pipeline (~22:00 BD time, after EOD snapshot).

    Enqueues run_ml_inference to the Celery ml queue so CPU-bound PyTorch/XGBoost
    work runs in the worker process — not in this scheduler event loop.
    """
    from extraction.jobs import job_run
    from extraction.tasks import run_ml_inference
    async with job_run("nightly_ml"):
        run_ml_inference.delay()
    logger.info("job_nightly_ml: enqueued run_ml_inference to ml queue")
```

Replace `job_quarterly` (`scheduler.py:600-608`):

```python
async def job_quarterly() -> None:
    """Quarterly ML retrain (Jan/Apr/Jul/Oct 1st, 03:00 BD time).

    Enqueues retrain_ml_models to the Celery ml queue. Worker handles full
    retrain (outcomes catchup → accuracy check → XGBoost + LSTM retrain → alert).
    """
    from extraction.jobs import job_run
    from extraction.tasks import retrain_ml_models
    async with job_run("quarterly"):
        retrain_ml_models.delay()
    logger.info("job_quarterly: enqueued retrain_ml_models to ml queue")
```

- [ ] **Step 4: Wrap `job_weekly_fundamentals` and `job_monthly`**

Replace `job_weekly_fundamentals` (`scheduler.py:561-574`):

```python
async def job_weekly_fundamentals() -> None:
    """Weekly fundamental scrape (Sunday 23:00) — multi-year EPS/NAV/PE/div for all tickers.

    ~18 min for 406 tickers at 3 concurrent, 1.5s delay.
    """
    from extraction.bulk_load.fundamentals_historical_loader import (
        bulk_load_fundamentals_historical,
    )
    from extraction.jobs import job_run
    logger.info("job_weekly_fundamentals: starting")
    async with job_run("weekly_fundamentals") as ctx:
        summary = await bulk_load_fundamentals_historical()
        ctx["records_inserted"] = summary["total_upserted"]
    logger.info(
        "job_weekly_fundamentals: complete ok=%d failed=%d upserted=%d",
        summary["ok"], summary["failed"], summary["total_upserted"],
    )
```

`job_monthly` already uses `job_run("monthly_macro")` (`scheduler.py:583`). Change
that name to `"monthly"` so it matches the registry:

```python
    async with job_run("monthly") as ctx:
```

(Leave the rest of `job_monthly` unchanged.)

- [ ] **Step 5: Run the existing scheduler/job tests**

Run: `pytest tests/unit -k "scheduler or jobs or live_prices" -v`
Expected: PASS (no regressions). If a test asserts a specific old `job_name`
string (e.g. `"monthly_macro"`), update that assertion to `"monthly"`.

- [ ] **Step 6: Lint + typecheck**

Run: `make check`
Expected: clean (ruff + mypy).

- [ ] **Step 7: Commit**

```bash
git add extraction/scheduler.py tests/
git commit -m "feat(scheduler): wrap 6 jobs in job_run for recovery ledger coverage"
```

---

## Task 8: Wire reconciler into boot + per-poll gap check

**Files:**
- Modify: `extraction/scheduler.py` (`start_scheduler`, `job_live_prices`)
- Test: `tests/unit/test_recovery.py`

- [ ] **Step 1: Write the failing test for the per-poll hook**

Append to `tests/unit/test_recovery.py`:

```python
@pytest.mark.asyncio
async def test_job_live_prices_calls_gap_check_when_open(monkeypatch):
    """job_live_prices invokes maybe_record_intraday_gap after the Open check."""
    import extraction.scheduler as sched

    called = {}

    async def fake_gap(now_bd):
        called["gap"] = True
        return False

    monkeypatch.setattr(sched, "get_market_status",
                        AsyncMock(return_value={"status": "Open"}))
    monkeypatch.setattr("extraction.recovery.maybe_record_intraday_gap", fake_gap)
    # Make the stream fetch blow up AFTER the gap hook so we don't touch the DB.
    monkeypatch.setattr("extraction.recovery._get_pool", AsyncMock())

    from extraction.base import AllAdaptersFailedError
    from extraction.registry import STREAMS
    monkeypatch.setitem(
        STREAMS, "live_prices",
        type("S", (), {"fetch": AsyncMock(side_effect=AllAdaptersFailedError("x"))})(),
    )

    with pytest.raises(AllAdaptersFailedError):
        await sched.job_live_prices()

    assert called.get("gap") is True
```

Note: this test confirms the hook fires before the fetch. The `job_run` context
will record a failed row — acceptable; the assertion is only about the hook.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_recovery.py::test_job_live_prices_calls_gap_check_when_open -v`
Expected: FAIL — gap hook not called (`called` empty / KeyError).

- [ ] **Step 3: Add the per-poll gap check to `job_live_prices`**

In `extraction/scheduler.py`, inside `job_live_prices`, immediately after the
market-open guard returns (right after `now = datetime.now(BD_TZ)` and the
`status != "Open"` early-return block at `scheduler.py:320-325`), add the gap
check before `async with job_run(...)`:

```python
    # Record any polling gap since the last snapshot (boot, network stall, missed
    # tick). Non-fatal; never blocks the pull.
    from extraction.recovery import maybe_record_intraday_gap
    await maybe_record_intraday_gap(now)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_recovery.py::test_job_live_prices_calls_gap_check_when_open -v`
Expected: PASS.

- [ ] **Step 5: Wire the reconciler into `start_scheduler`**

In `extraction/scheduler.py`, replace `start_scheduler` (`scheduler.py:1007-1011`):

```python
async def start_scheduler(scheduler: AsyncIOScheduler) -> None:
    """Start the scheduler, then kick off boot-time recovery in the background."""
    import asyncio

    configure_scheduler(scheduler)
    scheduler.start()
    logger.info("scheduler: started")

    # Converge missed daily/EOD jobs + resume live polling. Run as a background
    # task so a slow catch-up never blocks scheduler startup.
    from extraction.recovery import recover_missed_jobs
    asyncio.create_task(recover_missed_jobs())
```

- [ ] **Step 6: Write the failing test for boot wiring**

Append to `tests/unit/test_recovery.py`:

```python
@pytest.mark.asyncio
async def test_start_scheduler_kicks_off_recovery(monkeypatch):
    import extraction.scheduler as sched

    started = {}
    monkeypatch.setattr(sched, "configure_scheduler", lambda s: None)

    fake_recover = AsyncMock(return_value={"checked": 0})
    monkeypatch.setattr("extraction.recovery.recover_missed_jobs", fake_recover)

    class FakeScheduler:
        def start(self):
            started["started"] = True

    await sched.start_scheduler(FakeScheduler())
    # let the created task run
    import asyncio
    await asyncio.sleep(0)

    assert started["started"] is True
    fake_recover.assert_awaited_once()
```

- [ ] **Step 7: Run the boot-wiring test**

Run: `pytest tests/unit/test_recovery.py::test_start_scheduler_kicks_off_recovery -v`
Expected: PASS.

- [ ] **Step 8: Full recovery suite + lint/typecheck**

Run: `pytest tests/unit/test_recovery.py -v && make check`
Expected: all PASS; ruff + mypy clean.

- [ ] **Step 9: Commit**

```bash
git add extraction/scheduler.py tests/unit/test_recovery.py
git commit -m "feat(scheduler): boot-time reconciler + per-poll intraday gap check"
```

---

## Task 9: Full-suite regression + manual boot check

**Files:** none (verification only)

- [ ] **Step 1: Run the full unit suite**

Run: `make test`
Expected: all PASS (no regressions from the job_run rename or new module).

- [ ] **Step 2: Manual boot smoke (optional, needs db + redis)**

Run the scheduler module directly outside market hours:
`python -m extraction.scheduler`
Expected log lines: `scheduler: started`, then a `recovery_summary checked=9 ran=N skipped=M failed=0`. Outside market hours `_maybe_resume_live_prices` logs nothing (market Closed). With a fresh DB (no `pipeline_jobs` rows today), overdue jobs catch up once.

- [ ] **Step 3: Final commit (if any cleanup)**

```bash
git add -A
git commit -m "chore(recovery): regression pass"
```

---

## Self-Review Notes

**Spec coverage:**
- §3 Reconciler → Tasks 3, 4, 6, 7, 8 (predicate, registry, orchestration, ledger coverage, boot wiring). ✓
- §4 `market_gaps` ledger + detection → Tasks 1, 5, 8 (migration, recording, per-poll + boot hook). ✓
- §5 Intraday backfill → **deferred spike, not in this plan** (per spec §5/§8). ✓
- §6 Config → Task 2. ✓
- §7 Testing → unit tests across Tasks 2–8; integration folded into mocked-pool unit tests (matches the codebase's offline-first test strategy, CLAUDE.md). ✓

**Refinement vs spec:** Spec §4 lists two gap call sites (boot + per-poll). Plan centralizes *recording* in the per-poll hook inside `job_live_prices`; the boot path triggers one live pull (`_maybe_resume_live_prices`) which runs that same hook — same outcome, one code path, no double-record. Documented in Task 6/8.

**Type consistency:** `CatchUpJob` fields, `should_catch_up(now, anchor_hour, anchor_minute, is_run_day, last_success_date)`, `detect_gap(last_snapshot_time, now_utc, threshold_minutes, market_open)`, `maybe_record_intraday_gap(now_bd)`, `recover_missed_jobs() -> dict[str,int]` — names consistent across tasks. `job_run` names match registry `job_name` (`monthly_macro`→`monthly` rename in Task 7 Step 4).

**No placeholders:** every code step shows full code; TODO comments inside `job_eod_snapshot` are pre-existing and intentionally preserved.
```