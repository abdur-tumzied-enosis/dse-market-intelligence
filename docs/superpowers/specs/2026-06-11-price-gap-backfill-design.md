# Price Gap Backfill — Design

**Date:** 2026-06-11
**Status:** Approved

## Problem

When the scheduler is down across one or more trading days (host offline, Docker
stack stopped), those days' daily bars are never written to `stock_prices`.
Boot recovery (`extraction/recovery.py`) only catches up jobs that were due
*earlier today* — a fully missed prior day stays missing forever. Downstream,
`daily_ohlcv` / `weekly_ohlcv` / `monthly_ohlcv` / `sector_daily_stats` (and
every UI page reading them) show permanent holes.

## Goal

A self-healing daily job that scans the last 30 days for trading dates with no
`stock_prices` rows, backfills them from the `historical_ohlcv` adapter chain,
and refreshes the continuous aggregates so the data appears everywhere it
should.

## Decisions (user-confirmed)

| Decision | Choice |
|---|---|
| Trigger | New daily scheduler job + entry in `CATCHUP_REGISTRY` (boot catch-up) |
| Detection granularity | Market-wide: a date is missing when it has **zero** rows across all tickers |
| Fetch strategy | Per-ticker via existing `STREAMS["historical_ohlcv"]` chain (amarstock → bdshare failover); no new adapter |

## Architecture

New module: `extraction/gap_backfill.py`. Follows the `recovery.py` pattern —
pure decision functions (unit-testable offline) + async orchestrator with DB
access through a patchable `_get_pool()` indirection.

```
job_price_gap_backfill (scheduler, 18:00 BD daily; also in CATCHUP_REGISTRY)
        │
        ▼
backfill_missing_dates()                    extraction/gap_backfill.py
        │
        ├─ 1. present  = SELECT DISTINCT time::date FROM stock_prices
        │                WHERE time >= window_start
        ├─ 2. skip     = SELECT session_date FROM market_gaps
        │                WHERE reason = 'backfill_no_data'
        ├─ 3. missing  = find_missing_dates(present, skip, today, window_days)   [pure]
        │
        ├─ if missing empty → log, done (normal-day path: one cheap query)
        │
        ├─ 4. for each active ticker (3 concurrent, throttled):
        │        STREAMS["historical_ohlcv"].fetch(ticker, start=min(missing),
        │                                          end=max(missing))
        │        keep only rows whose date ∈ missing
        │        INSERT ... ON CONFLICT (time, ticker) DO NOTHING
        │
        ├─ 5. re-check: dates still empty → market_gaps row
        │        (reason='backfill_no_data', recovered=true) → excluded next scan
        │
        ├─ 6. CALL refresh_continuous_aggregate(view, gap_start, gap_end + 1d)
        │        for daily_ohlcv, weekly_ohlcv, monthly_ohlcv, sector_daily_stats
        │
        └─ 7. WARNING alert when gaps were found (recovered + unrecoverable counts)
```

## Components

### `find_missing_dates(present, skip, today, window_days=30) -> list[date]` — pure

- Expected dates: every Sun–Thu date in `[today - window_days, today - 1]`.
  Today is excluded — `job_live_prices` / `job_eod_snapshot` own today's bar.
- Missing = expected − present − skip.
- Weekday set reuses the Sun–Thu convention (`{6, 0, 1, 2, 3}` in Python
  `weekday()` terms) already used by `recovery.py` and `gap_report.py`.

### `backfill_missing_dates() -> dict` — async orchestrator

- Returns summary `{"missing": n, "recovered_dates": n, "no_data_dates": n,
  "rows_inserted": n, "tickers_failed": n}`.
- Never raises (same contract as `recover_missed_jobs`); catches and logs.
- Fetch loop: active tickers from `companies WHERE is_active`, semaphore of 3,
  per-ticker failure logged and counted, never aborts the loop.
- Insert uses `ON CONFLICT (time, ticker) DO NOTHING` (migration 008 unique
  index) — backfill must never clobber rows written by live/EOD jobs.
- `time` convention: trading date at 00:00 UTC (matches bdshare/amarstock
  historical adapters and `daily_ohlcv` bucketing).
- Quality rules: fetched frames pass through the same `historical_ohlcv`
  required-column check as the bulk loaders (`extraction/quality.py`).

### Holiday handling

DSE holidays inside the window look identical to missed days until we try to
fetch them. After the fetch pass, any date still empty across all tickers is
recorded in `market_gaps` (`session_date=<date>`, `gap_start`/`gap_end` = day
bounds UTC, `reason='backfill_no_data'`, `recovered=true` — nothing left to
recover) and excluded from future scans. No retry loop, no holiday calendar
dependency.

### CA refresh

`daily_ohlcv`'s refresh policy has `start_offset = 3 days` (migration 003) —
rows backfilled for older dates are otherwise never materialized. After any
successful insert, run `CALL refresh_continuous_aggregate(view, lo, hi)` for
`daily_ohlcv`, `weekly_ohlcv`, `monthly_ohlcv`, `sector_daily_stats` over
`[min(missing), max(missing) + 1 day)`. Note: must run outside a transaction
(asyncpg autocommit `execute` is fine); hierarchical CAs refresh in dependency
order (daily first).

### Scheduler + recovery wiring

- `extraction/scheduler.py`: new `job_price_gap_backfill()` wrapped in
  `job_run("price_gap_backfill")`; cron daily 18:00 Asia/Dhaka (after EOD
  14:35, sector_pe 15:45, daily_ohlcv policy refresh 15:00 BD).
- `extraction/recovery.py`: `CatchUpJob("price_gap_backfill", ..., anchor 18:00,
  _is_every_day, dep_rank=3)` appended to `_build_registry()`.
- `mgmt/config.py`: `gap_backfill_enabled: bool = True`,
  `gap_backfill_window_days: int = 30`.

## Error handling

- All-adapters-failed for one ticker → count in `tickers_failed`, continue.
- DB unavailable → log error, return summary with zeros (never crash scheduler
  or boot recovery).
- CA refresh failure → log warning, still report inserts (data is in
  `stock_prices`; next policy run within the 3-day window may cover recent
  dates, older ones surface in the next backfill run's alert).
- Alert: WARNING via `fire_alert` whenever `missing > 0`, with recovered /
  no-data breakdown.

## Testing

- **Unit (offline):** `find_missing_dates` — full window present, weekend
  exclusion, today exclusion, skip-list exclusion, empty DB, window boundary.
- **Unit (patched DB/stream):** orchestrator happy path; holiday path (fetch
  returns nothing for a date → market_gaps write); per-ticker failure
  tolerance; ON CONFLICT no-clobber; CA refresh called with correct range;
  disabled flag short-circuits. Same patching pattern as
  `tests/unit/test_recovery*.py`.

## Out of scope (YAGNI)

- Per-ticker hole detection (suspended/illiquid tickers → false positives).
- Market-wide `'All Instrument'` bdshare fetch (unverified; revisit if
  per-ticker proves too slow in practice).
- Backfilling `intraday_prices` (snapshots are unrecoverable after the fact).
- Holiday calendar integration.
