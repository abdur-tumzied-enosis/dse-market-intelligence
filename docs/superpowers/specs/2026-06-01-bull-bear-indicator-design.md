# Bull vs Bear Market Indicator — Design

**Date:** 2026-06-01
**Status:** Approved (design); pending implementation plan

## Goal

Add a Bull vs Bear regime card to the market dashboard
(`frontend/app/(app)/dashboard/page.tsx`, "Market Overview"). The regime is
decided by **DSEX index trend vs its 50-day moving average**:

- DSEX last close **≥** 50-day MA → **Bull**
- DSEX last close **<** 50-day MA → **Bear**

## Decisions (locked during brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Signal | DSEX index trend vs moving average | Simple, regime-level (not single-day noise) |
| MA history source | Persist daily, grow toward 50d; bootstrap by backfilling 30d from bdshare | Robust long-term; backfill makes it usable on day one |
| Warm-up behavior (< 50 days) | Provisional shorter MA over the longest window available, labeled "provisional" | Works from day one, sharpens as data accrues |

## Problem context

No DSEX history is stored anywhere today. The index adapters
(`dse_direct/market_info.py` priority 1, `bdshare/market_info.py` priority 2)
return only a live snapshot; nothing persists index values to the DB, and there
is no index-history table. A 50-day MA therefore requires building history that
does not yet exist.

Key reusable fact: `bd.get_market_info()` already returns a **30-day history**
(columns include `DSEX Index`, `DS30 Index`, `DSES Index` per trading day). The
bdshare adapter currently keeps only row 0 and discards rows 1–29. We exploit
the full 30 rows for the one-time backfill.

## Architecture

```
bd 30d backfill ─┐
                 ├─→ index_daily ──→ GET /market/regime (MA calc) ──→ RegimeCard
eod daily snap ──┘
```

Two writers feed one table; one read endpoint computes the regime; one frontend
card renders it.

## Components

### 1. Storage — `db/migrations/029_index_daily.sql`

One row per trading day. All three indices stored (they arrive in the same
fetch, so storing DS30/DSES is free and keeps the table generally useful).

```sql
CREATE TABLE IF NOT EXISTS index_daily (
    date        date PRIMARY KEY,
    dsex        numeric,
    ds30        numeric,
    dses        numeric,
    source      text NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now()
);
```

Idempotent (`IF NOT EXISTS`), applied in order by the existing `db/migrate.py`
(tracked in `_migrations`).

### 2. Bootstrap — `backfill_index_history()`

One-shot loader (new function under `extraction/`, e.g.
`extraction/bulk_load/index_history_loader.py`). Steps:

1. Call `bd.get_market_info()` → 30-day DataFrame.
2. Map each row's `Date` + `DSEX/DS30/DSES Index` columns to an `index_daily` row.
3. `INSERT ... ON CONFLICT (date) DO NOTHING` so re-runs are safe.

Exposed via a `make` target (e.g. `make backfill-index-history`). Run once after
the migration. Result: ~30 days seeded → provisional MA available immediately.

### 3. Daily snapshot — extend `job_eod_snapshot`

`job_eod_snapshot` (in `extraction/scheduler.py`) already runs once at close on
trading days and is currently a stub. Add a helper `_persist_index_snapshot()`
called from it:

1. Fetch the `market_indices` stream (`STREAMS["market_indices"].fetch()`).
2. Build today's row (Dhaka trading date) from DSEX/DS30/DSES values.
3. `INSERT ... ON CONFLICT (date) DO UPDATE` into `index_daily`.

No new scheduler job and no new cron — reuses the existing trading-day EOD
trigger. Note: test mode (`pipeline_test_mode`) also runs `eod_snapshot` on a
compressed interval (`test_eod_snapshot_minutes`), so the snapshot path exercises
under test mode too — the `ON CONFLICT (date) DO UPDATE` keeps repeated same-day
runs converging on one row.

### 4. Backend — regime computation + endpoint

New endpoint `GET /market/regime` in `api/routers/market.py`. Kept separate from
`/indices` because it is daily-cadence data with its own cache TTL (~300s),
unlike the 60s live-indices cache.

New schema in `api/schemas/market.py`:

```python
class MarketRegime(BaseModel):
    regime: str                  # "Bull" | "Bear" | "Unknown"
    dsex: float | None           # latest stored DSEX close
    ma: float | None             # moving average over `window` days
    window: int                  # actual trading days used (≤ 50)
    provisional: bool            # window < 50
    distance_pct: float | None   # (dsex - ma) / ma * 100
    as_of: str | None            # latest date present in index_daily (ISO)
    data_status: str             # "ok" | "provisional" | "insufficient"
```

Logic (reads `index_daily`, DSEX column, ordered by date):

- Load DSEX series (most recent first).
- `window = min(50, len(series))`.
- `ma = mean(last `window` DSEX values)`.
- `regime = "Bull" if dsex >= ma else "Bear"`.
- `provisional = window < 50` → `data_status = "provisional"`, else `"ok"`.
- **Floor:** if `len(series) < 5` → `regime="Unknown"`, `ma=None`,
  `data_status="insufficient"` (avoid a regime call on too-thin data).
- `distance_pct = (dsex - ma) / ma * 100` when `ma` is set and non-zero.

Always returns **200** (derived/cached data — never 502). Empty table →
`Unknown` / `insufficient`.

### 5. Frontend — RegimeCard

- `frontend/lib/types.ts`: add `MarketRegime` interface matching the schema.
- `frontend/lib/server-api.ts`: add
  `regime: () => serverGet<MarketRegime>('/api/market/regime')` to the `market`
  client.
- `frontend/components/market/RegimeCard.tsx`: new card.
  - Bull → green accent; Bear → red accent; Unknown → muted.
  - Shows the regime word, DSEX vs MA values, and `distance_pct`.
  - When `provisional`, shows a sub-label like `provisional (32/50d)` using
    `window`.
  - When `data_status === "insufficient"`, shows a neutral "collecting data"
    state.
  - Style matches the existing `IndexCard` (same surface/border/typography).
- `frontend/app/(app)/dashboard/page.tsx`: add `serverApi.market.regime()` to
  the existing `Promise.allSettled`, render `<RegimeCard>` in the index-card
  row. On rejection the card is omitted (matches the existing null-tolerant
  pattern for indices/movers/heatmap).

### 6. Tests

- **Unit** (regime calc): bull (dsex ≥ ma), bear (dsex < ma), provisional
  (window < 50), insufficient (< 5 days), empty series.
- **API** (`tests/unit/` following `test_api_market_indices.py`): `/market/regime`
  response shape + insufficient-data path.
- **Frontend** (`frontend/__tests__/components/market/RegimeCard.test.tsx`,
  matching the existing IndexCard/MoverStrip/HeatmapGrid tests): bull, bear, and
  provisional rendering.

## Error handling

- Regime endpoint never raises 502: empty/thin `index_daily` → `Unknown`/
  `insufficient`, HTTP 200.
- Backfill and daily snapshot both use `ON CONFLICT` so re-runs are idempotent.
- Daily snapshot failure is logged and non-fatal (the EOD job continues); the
  next trading day's run recovers.

## Out of scope (YAGNI)

- Breadth (advancers/decliners) and composite scoring as regime inputs.
- Historical regime timeline / chart.
- Alerts on regime flip (Bull→Bear).
- Backfilling more than 30 days (no source provides longer DSEX history cheaply).
