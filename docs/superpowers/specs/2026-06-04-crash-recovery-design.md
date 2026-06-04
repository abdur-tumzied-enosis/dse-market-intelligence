# Crash / Downtime Recovery Design

**Date:** 2026-06-04
**Status:** Approved design — ready for implementation plan
**Scope:** Make the extraction pipeline fault-tolerant across scheduler crashes and host
shutdowns, so that on restart the system converges to the state it *should* be in.

---

## 1. Problem

The scheduler (`extraction/scheduler.py`) runs APScheduler with a PostgreSQL job store.
Jobs survive restarts, but recovery of *missed* runs relies entirely on APScheduler's
`misfire_grace_time` + `coalesce`. This leaves three gaps:

1. **Missed daily/EOD jobs are silently dropped.** If the host is down through a job's
   anchor time and past its grace window, APScheduler discards the missed fire. Example:
   host down through 22:00 → `nightly_ml` never runs that day; down through 14:35 →
   `eod_snapshot` never writes the index row / 52-week update for the day. No catch-up.

2. **No visibility into live-price polling gaps.** When the scheduler is down during
   market hours (Sun–Thu 10:00–14:30 BD), `intraday_prices` simply has missing rows. The
   daily converging bar in `stock_prices` self-heals (per-day bucket + `ON CONFLICT DO
   UPDATE`), and session-cumulative volume/value self-recovers on the next poll. The only
   permanent loss is **intraday price granularity** (intermediate open/high/low ticks).
   Nothing records that a gap happened, so downstream consumers can't distinguish a real
   thin-trading bar from a gapped one.

3. **Open-trigger can be lost.** `job_market_status_open` (cron 10:00–10:15) fires the
   first live pull at market open. If the outage spans that whole window, the trigger is
   lost for the day (cosmetic — the `*/1` live-price cron compensates within a minute).

### Key data-model facts that shape the solution

- Live feeds report **session-cumulative** volume/value/trades. A single post-gap snapshot
  carries the full cumulative totals, so volume/value are *not* lost across a gap — the
  `*_final` delta views just attribute the whole gap to one fat bar. Only price ticks are lost.
- `pipeline_jobs` is a per-run ledger (`job_name`, `started_at`, `finished_at`, `status`).
  This is the source of truth for "did job X run successfully today".
- The only historical OHLCV source in the adapter registry (`historical_ohlcv` stream:
  AmarStock CSV + BDShare) is **daily** resolution. There is **no intraday-tick history
  source** currently wired in.

---

## 2. Chosen approach

**Convergence on boot, driven by the `pipeline_jobs` ledger** (Approach A from
brainstorming), plus two supporting pieces. Rejected alternatives: widening
`misfire_grace_time` (opaque, fires only the single most-recent miss, no dependency
ordering, no observability) and a durable task outbox (over-engineered for ~9 anchored
jobs; overlaps half-built Celery).

Three pieces:

1. **Boot-time reconciler** — catch up missed daily/EOD/periodic jobs.
2. **`market_gaps` ledger + gap detection** — make live-price gaps visible and resume
   polling immediately on boot.
3. **Intraday backfill** — deferred discovery spike; build only if a tick source exists.

---

## 3. Piece 1 — Boot-time Reconciler (`extraction/recovery.py`)

### Entry point

New coroutine `recover_missed_jobs()`. Called from `start_scheduler()` **after**
`scheduler.start()`, scheduled as a one-shot background task (`asyncio.create_task`) so it
does not block scheduler startup. Emits a single structured `recovery_summary` log line
(jobs checked / ran / skipped / failed). Guarded by a `recovery_enabled` config flag
(default `True`).

### Catch-up registry

A declarative, in-module list — one entry per anchored daily/periodic job:

```
CatchUpJob(job_name, func, anchor_hour, anchor_minute, run_day_predicate, dep_rank)
```

| job_name | func | anchor (BD) | runs on | dep_rank |
|---|---|---|---|---|
| daily_macro | `job_daily_macro` | 02:00 | every day | 0 |
| news_sentiment | `job_news_sentiment` | 03:30 | every day | 0 |
| seed_companies | `job_seed_companies` | 08:00 | every day | 1 |
| eod_snapshot | `job_eod_snapshot` | 14:35 | market days | 2 |
| sector_pe | `job_sector_pe` | 15:45 | market days | 3 |
| nightly_ml | `job_nightly_ml` | 22:00 | every day | 4 |
| weekly_fundamentals | `job_weekly_fundamentals` | Sun 23:00 | sunday | 5 |
| monthly | `job_monthly` | 1st 01:00 | day == 1 | 5 |
| quarterly | `job_quarterly` | 1,4,7,10 / 1st 03:00 | month in {1,4,7,10} and day == 1 | 5 |

- "market days" = `cfg.live_prices_market_days` (`sun,mon,tue,wed,thu`).
- Anchor times read from `mgmt.config.Settings` where available (not re-hardcoded):
  `daily_macro_hour/minute`, `eod_snapshot_hour/minute`, `weekly_fundamentals_*`,
  `monthly_*`, `quarterly_*`. `news_sentiment` (03:30), `seed_companies` (08:00),
  `sector_pe` (15:45), `nightly_ml` (22:00) are currently hardcoded in
  `_configure_production_mode`; the registry references the same literals (a follow-up may
  promote these to config, out of scope here).

**Excluded from catch-up:**
- Interval jobs (`announcements`, `news_scrape`, `health_checks`) — APScheduler reschedules
  them from boot time; the next interval covers any miss.
- `live_prices`, `market_status_open`, `market_status_close` — intraday; handled by Piece 2.

### Decision predicate (pure, unit-testable)

For each registry entry, evaluated in ascending `dep_rank` order:

```
now      = datetime.now(BD_TZ)
overdue  = run_day_predicate(now)  AND  anchor_datetime_today <= now
not_done = NOT exists(pipeline_jobs row WHERE job_name = X
                                        AND status = 'success'
                                        AND (started_at AT TIME ZONE 'Asia/Dhaka')::date = now.date())
should_run = overdue AND not_done
```

The predicate is factored into a pure function
`should_catch_up(now, anchor_hour, anchor_minute, run_day_predicate, last_success_date) -> bool`
so it can be unit-tested without a DB. The `last_success_date` lookup is the only
DB-touching part and is injected.

### Execution

- Jobs run **sequentially in `dep_rank` order** (v1 — simplicity over parallelism).
- Each catch-up invocation is wrapped in `try/except`; one failure logs + continues to the
  next job (failures already fire alerts via `job_run`).
- A catch-up run is an ordinary call to the job function, so it records its own
  `pipeline_jobs` row — a second boot in the same day will then see it as done and skip.

### Idempotency

Every catch-up target write is already UPSERT or dedup-safe:
- `stock_prices` — `ON CONFLICT (time, ticker) DO UPDATE`
- `index_daily` — `ON CONFLICT (date) DO UPDATE`
- `macro_indicators` — `ON CONFLICT (indicator, period, source) DO UPDATE`
- `sector_pe` — append-only, but `/api/sectors` reads latest-per-sector (`DISTINCT ON`)
- `nightly_ml` / `quarterly` — enqueue a Celery task (`.delay()`); re-enqueue is acceptable
- `seed_companies` — upserts roster + enrichment
- `news_sentiment` — scores only unscored articles

Re-running a partially-completed catch-up is therefore safe.

### Prerequisite — uniform ledger coverage

The reconciler's `not_done` check depends on every catch-up-eligible job leaving a
`pipeline_jobs` row. These currently do **not** wrap `job_run()` and must be updated to do
so (minimal change — wrap body in `async with job_run(<name>) as ctx:`):

- `job_seed_companies`
- `job_eod_snapshot`
- `job_nightly_ml`
- `job_weekly_fundamentals`
- `job_monthly`
- `job_quarterly`

Already wrapped (no change): `job_daily_macro`, `job_sector_pe`, `job_news_sentiment`.
The `job_name` passed to `job_run()` must exactly match the registry `job_name`.

---

## 4. Piece 2 — `market_gaps` ledger + gap detection

### Migration `035_market_gaps.sql`

```sql
CREATE TABLE IF NOT EXISTS market_gaps (
    id           BIGSERIAL PRIMARY KEY,
    session_date DATE        NOT NULL,
    gap_start    TIMESTAMPTZ NOT NULL,   -- last snapshot instant before the gap (UTC)
    gap_end      TIMESTAMPTZ NOT NULL,   -- first snapshot instant after the gap (UTC)
    reason       TEXT        NOT NULL DEFAULT 'scheduler_downtime',
    recovered    BOOLEAN     NOT NULL DEFAULT FALSE,  -- set true once backfilled
    detected_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_market_gaps_session_date ON market_gaps (session_date);
CREATE INDEX IF NOT EXISTS idx_market_gaps_unrecovered  ON market_gaps (recovered) WHERE recovered = FALSE;
```

### Detection (`extraction/recovery.py` helper, also called from `job_live_prices`)

Pure helper:

```
detect_gap(last_snapshot_time, now_utc, threshold_minutes, market_open: bool)
    -> (gap_start, gap_end) | None
```

Returns a gap window when `market_open` is true and
`now_utc - last_snapshot_time > threshold_minutes`. `threshold_minutes` =
`cfg.intraday_gap_threshold_minutes` (default `3` — one missed `*/1` tick of slack).

Two call sites:

1. **On boot** — inside `recover_missed_jobs()`, if today is a market day and current BD
   time is within market hours (`get_market_status()` == `Open`): read
   `MAX(time)` from `intraday_prices` for today; if `detect_gap(...)` returns a window,
   insert a `market_gaps` row, fire a `WARNING` alert via `fire_alert`, then **immediately
   `await job_live_prices()`** so polling resumes without waiting for the next cron tick
   (this also mitigates a lost open-trigger).

2. **Per-poll** — at the start of `job_live_prices()` (after the `status == "Open"` check,
   before fetch): compare `now` against the last `intraday_prices` row; if a gap is
   detected, record it. This catches gaps from *any* cause (network stall, container
   restart, single missed poll), not just full reboots.

A boundary unique guard prevents duplicate rows for the same gap: skip insert if an
unrecovered row already exists with the same `gap_start` (within the session).

### Consumer benefit (v1 = record only)

`market_gaps` is the durable record of every hole. Future work (out of scope here): the
intraday OHLCV API / UI can left-join `market_gaps` to flag bars overlapping a gap window
as "gapped / partial" so a fat post-gap volume bar isn't read as a real spike.

---

## 5. Piece 3 — Intraday backfill (deferred discovery spike)

**Not committed to implementation.** Time-boxed investigation with a go/no-go decision.

### Spike goal

Determine whether AmarStock's charting backend (the source behind the intraday charts on
`amarstock.com`) exposes an intraday-candle endpoint (resolution + from/to params) usable
to backfill the `market_gaps` windows. Method: browser network intercept on a chart load,
mirroring how the existing AmarStock JSON endpoints were discovered.

### If a source is found (then, and only then)

- Add an intraday-history adapter under `extraction/adapters/amarstock/`.
- Add a backfill job: read `market_gaps WHERE recovered = FALSE`, fetch candles per ticker
  for each gap window, insert into `intraday_prices` (natural dedup on `(time, ticker)` is
  *not* enforced by a constraint today — backfill must `SELECT`-guard or the migration must
  add a partial unique index; decide during plan), then `refresh_continuous_aggregate` for
  the affected range across `ohlcv_5m..60m`, and set `recovered = TRUE`.

### If no source is found

Document infeasibility in a short decision note. `market_gaps` stays observability-only;
affected bars remain flagged. No code is committed beyond the throwaway probe.

---

## 6. Configuration additions (`mgmt/config.py`)

| Setting | Default | Purpose |
|---|---|---|
| `recovery_enabled` | `True` | Master switch for `recover_missed_jobs()` on boot |
| `intraday_gap_threshold_minutes` | `3` | Min gap (minutes) before recording a `market_gaps` row |

---

## 7. Testing strategy

**Unit (offline, pure functions):**
- `should_catch_up(...)` — matrix over: anchor before/after `now`; run-day true/false;
  last-success date == today / != today / null → expected bool.
- `detect_gap(...)` — last snapshot within / beyond threshold; `market_open` true/false;
  null last snapshot (first poll of day) → expected window / `None`.
- Catch-up registry well-formedness — every `job_name` is unique and matches a real job.

**Integration (db + redis):**
- Seed `pipeline_jobs` with a today-success row for job X → reconciler skips X.
- No row for X and anchor passed → reconciler runs X once and a `pipeline_jobs` row appears.
- Gap detection: seed `intraday_prices` with a stale max(time) during simulated market
  hours → a `market_gaps` row is written and `job_live_prices` is triggered.
- Idempotency: run `recover_missed_jobs()` twice → second pass is a no-op.

**Migration:** `035_market_gaps.sql` applies cleanly and is idempotent (`IF NOT EXISTS`).

---

## 8. Out of scope

- Promoting hardcoded job timings (`news_sentiment`, `seed_companies`, `sector_pe`,
  `nightly_ml`) into config.
- UI / API flagging of gapped intraday bars.
- Backfill implementation (gated behind the Piece 3 spike result).
- Parallelizing same-rank catch-up jobs.
- Recovery for interval jobs.

---

## 9. Files touched

| File | Change |
|---|---|
| `extraction/recovery.py` | **new** — reconciler, registry, `should_catch_up`, `detect_gap` |
| `extraction/scheduler.py` | call `recover_missed_jobs()` from `start_scheduler()`; wrap 6 jobs in `job_run()`; per-poll gap check in `job_live_prices` |
| `db/migrations/035_market_gaps.sql` | **new** — `market_gaps` table |
| `mgmt/config.py` | `recovery_enabled`, `intraday_gap_threshold_minutes` |
| `tests/unit/test_recovery.py` | **new** — pure-function tests |
| `tests/integration/test_recovery.py` | **new** — ledger + gap detection tests |
