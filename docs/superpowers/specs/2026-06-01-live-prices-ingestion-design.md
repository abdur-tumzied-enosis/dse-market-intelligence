# Live Prices Ingestion + On-Demand FE Polling — Design

**Date:** 2026-06-01
**Status:** Approved (pending spec review)

## Problem

`job_live_prices()` (`extraction/scheduler.py`) is a stub — it only invalidates
cache, with `# TODO: implement ingest_live_prices()`. The `live_prices` DataStream
(`STREAMS["live_prices"]`: dse_direct → bdshare → amarstock failover chain) is
defined but never consumed by any job. Nothing writes intraday/live prices into
`stock_prices`.

Consequences:
- `GET /stocks/{ticker}` `latest_price` reads `stock_prices ORDER BY time DESC
  LIMIT 1` → returns the last **backfilled EOD** bar, not a live price.
- The stock detail page (`frontend/app/(app)/stocks/[ticker]/page.tsx`) is a
  server component; price is rendered once at SSR and never refreshes.

## Goal

1. Implement `ingest_live_prices()` so the scheduler writes live prices into
   `stock_prices` during market hours.
2. Let the frontend show live per-ticker prices, polling every 2 minutes while
   the market is open.

## Critical Constraint: Cumulative Volume

The live feeds (bdshare `get_current_trade_data`, amarstock `LatestPrice`,
dse_direct scrape) report **session-cumulative** `volume`/`value`/`trades`, not
per-interval deltas. The `daily_ohlcv` continuous aggregate (migration 003) does
`SUM(volume)`, `SUM(trades)`, `SUM(value_bdt)`.

Therefore we MUST NOT append a fresh `stock_prices` row every 2 minutes — doing
so would sum cumulative snapshots and inflate daily volume ~135×.

**Solution:** UPSERT exactly **one row per ticker per trading day**. The row's
`time` is the trading-day bucket (today at 00:00 Asia/Dhaka, stored as
TIMESTAMPTZ). Each run does `ON CONFLICT (time, ticker) DO UPDATE`, so the row
converges to the latest snapshot. `daily_ohlcv`'s `SUM(volume)` over a single
row equals that day's cumulative volume — correct. `latest_price` stays fresh.
The EOD snapshot job can later overwrite the same bucket with the official close.

The unique index `idx_stock_prices_time_ticker_unique ON stock_prices (time,
ticker)` (migration 008) provides the conflict target.

## Components

### 1. `ingest_live_prices()` — `extraction/scheduler.py`

Replaces the stub body of `job_live_prices()`.

- Guard: only write when `_market_status() == "Open"` (reuse the trading-hours
  logic from `api/routers/market.py`, or inline equivalent). Outside hours: skip
  the write, still invalidate cache, return.
- `result = await STREAMS["live_prices"].fetch()` — wrapped so
  `AllAdaptersFailedError` is logged and the job records failure (via `job_run`
  context, matching other jobs) rather than crashing the scheduler.
- Compute `bucket_time = datetime.now(Asia/Dhaka).replace(hour=0, minute=0,
  second=0, microsecond=0)`.
- For each record in `result.data`:
  - `ticker` — skip if blank.
  - `close` = `rec["ltp"]` if present else `rec["close"]`; skip row if None.
  - `change_pct` = `rec["change_pct"]`; if None and `prev_close` present and > 0,
    compute `(close - prev_close) / prev_close * 100`.
  - `value_bdt` = `rec["value_bdt"]` if present else `rec["value_mn"] * 1_000_000`.
  - `high`, `low`, `prev_close`, `volume`, `trades`, `open` (None for live feeds).
  - `source` = `result.source_name`; `quality_flag = 'live'`.
- UPSERT:
  ```sql
  INSERT INTO stock_prices
      (time, ticker, open, high, low, close, volume, trades,
       value_bdt, prev_close, change_pct, source, ingested_at, quality_flag)
  VALUES ($1,...,$14)
  ON CONFLICT (time, ticker) DO UPDATE SET
      close=EXCLUDED.close, high=EXCLUDED.high, low=EXCLUDED.low,
      volume=EXCLUDED.volume, trades=EXCLUDED.trades,
      value_bdt=EXCLUDED.value_bdt, change_pct=EXCLUDED.change_pct,
      prev_close=EXCLUDED.prev_close, source=EXCLUDED.source,
      ingested_at=NOW(), quality_flag=EXCLUDED.quality_flag
  ```
  `open` is intentionally NOT updated on conflict (live feeds have no open;
  preserve any value an EOD/backfill row may have set).
- Record `records_inserted`/`records_fetched` in the `job_run` ctx.
- Keep existing cache invalidation (`cache:pipeline_status:*`, `cache:live_prices*`,
  plus `cache:live_snapshot`, `cache:api:stocks:detail:*`, `cache:api:market:*`).

### 2. Schedule — `mgmt/config.py`

- `live_prices_minutes`: `"0,15,30,45"` → `"*/2"` (every 2 min).
- Hours (`10-14`) and days (`mon,tue,wed,thu,sun`) unchanged.
- `test_live_prices_minutes` already `2` — unchanged.
- The cron in `_configure_production_mode` already reads `cfg.live_prices_minutes`
  — no scheduler code change needed.

### 3. Live endpoint — `api/routers/stocks.py`

`GET /stocks/{ticker}/live` (auth: `get_current_user`, matching the rest of the
router).

- Shared snapshot cache: fetch the full `live_prices` records once, cache under
  `cache:live_snapshot` with ttl 75s, and filter the requested ticker out of it.
  This avoids hitting the source once per ticker.
- 404 if the ticker is unknown to `companies`.
- If the ticker is absent from the snapshot (e.g. not traded): return
  `{ticker, available: false, market_status, as_of}`.
- Response shape:
  ```json
  {
    "ticker": "CITYBANK",
    "available": true,
    "ltp": 23.4, "high": 23.9, "low": 23.1, "prev_close": 23.0,
    "change_pct": 1.74, "volume": 1234567, "value_bdt": 28900000.0,
    "market_status": "Open",
    "as_of": "2026-06-01T08:30:00Z"
  }
  ```
- Reuse `_f()` / ltp / value coercion helpers (extract shared helpers or
  duplicate minimal versions; market.py already has them — prefer a small shared
  util if clean, else local).

### 4. Frontend — `frontend/components/stocks/LivePrice.tsx` (new, client)

- `'use client'`. Props: `ticker: string`, `initial` (the SSR `latest_price`
  object, may be null).
- Renders the price + change% + HIGH/LOW/VOLUME/VALUE strip currently inline in
  the header card. The detail page passes `latest_price` as `initial` for instant
  first paint (no flash, no waterfall).
- Polling: `useEffect` + `setInterval` every **120_000 ms**. Fetch
  `/api/stocks/${ticker}/live` via `get` from `@/lib/api`. Only poll while the
  last response's `market_status === "Open"`; when closed, render the last known
  values and clear the interval. Clean up interval on unmount.
- Indicator: pulsing dot + `as of HH:MM` (local time) when live; static when
  market closed.
- Detail page (`page.tsx`) stays a server component: replace the inline price/
  OHLV block with `<LivePrice ticker={company.ticker} initial={latest_price} />`.

### 5. Tests

- `tests/unit/` — extend existing patterns (`test_api_market.py`):
  - `ingest_live_prices`: feed a fake `live_prices` stream; assert two runs in the
    same trading day produce **one** `stock_prices` row (not two), and that volume
    is the latest snapshot's value (not summed). Assert `change_pct` computed when
    feed omits it. Assert skip when market closed.
  - `/stocks/{ticker}/live`: 200 with filtered ticker, 404 unknown ticker,
    `available:false` when ticker absent from snapshot, cache reuse.

## Out of Scope (YAGNI)

- SSE / websocket per-ticker streaming (market indices already have `/market/stream`;
  not extending to per-ticker).
- A dedicated intraday tick-history table.
- Backfilling intraday history.

## Files Touched

| File | Change |
|---|---|
| `extraction/scheduler.py` | implement `ingest_live_prices()` in `job_live_prices` |
| `mgmt/config.py` | `live_prices_minutes` → `"*/2"` |
| `api/routers/stocks.py` | new `GET /{ticker}/live` |
| `api/schemas/stocks.py` | `LivePriceResponse` schema |
| `frontend/components/stocks/LivePrice.tsx` | new client component |
| `frontend/app/(app)/stocks/[ticker]/page.tsx` | use `<LivePrice>` |
| `frontend/lib/types.ts` | `LivePrice` type (if typed fetch used) |
| `tests/unit/test_*` | ingest + endpoint tests |
