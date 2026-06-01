# Live Prices Ingestion + On-Demand FE Polling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the existing `live_prices` DataStream into the scheduler so live prices land in `stock_prices` during market hours, and let the stock detail page poll a new per-ticker live endpoint every 2 minutes.

**Architecture:** `job_live_prices()` fetches the `live_prices` failover chain and UPSERTs **one row per ticker per trading day** (time bucketed to Asia/Dhaka midnight) so cumulative-volume snapshots don't inflate the `daily_ohlcv` `SUM(volume)` aggregate. A new `GET /stocks/{ticker}/live` endpoint serves a shared 75s-cached snapshot. A client component polls it every 120s while the market is open.

**Tech Stack:** Python 3.12, APScheduler, asyncpg, FastAPI, Pydantic, TimescaleDB; Next.js 16 App Router, React 19, TypeScript.

**Design spec:** `docs/superpowers/specs/2026-06-01-live-prices-ingestion-design.md`

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `extraction/scheduler.py` | `job_live_prices` body, pure row-mapping helper, market-status + cache helpers | Modify |
| `mgmt/config.py` | `live_prices_minutes` cadence | Modify |
| `api/schemas/stocks.py` | `LivePrice` response model | Modify |
| `api/routers/stocks.py` | `_live_snapshot()` + `GET /{ticker}/live` | Modify |
| `frontend/lib/types.ts` | `LivePrice` TS interface | Modify |
| `frontend/components/stocks/LivePrice.tsx` | client component: render + poll | Create |
| `frontend/app/(app)/stocks/[ticker]/page.tsx` | mount `<LivePrice>` | Modify |
| `tests/unit/test_live_prices_ingest.py` | row-mapping unit tests | Create |
| `tests/unit/test_api_stocks_live.py` | endpoint unit tests | Create |

---

### Task 1: Pure row-mapping helper + market-status guard (`extraction/scheduler.py`)

Extract the snapshot→DB-row transform into a pure function so it is unit-testable without a DB, plus a market-hours guard.

**Files:**
- Modify: `extraction/scheduler.py`
- Test: `tests/unit/test_live_prices_ingest.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_live_prices_ingest.py`:

```python
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
    assert _market_is_open(BD.localize(datetime(2026, 6, 1, 11, 0)))   # Sun 11:00
    assert not _market_is_open(BD.localize(datetime(2026, 6, 1, 15, 0)))  # Sun 15:00 (after close)
    assert not _market_is_open(BD.localize(datetime(2026, 6, 5, 11, 0)))  # Fri 11:00
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_live_prices_ingest.py -v`
Expected: FAIL — `ImportError: cannot import name '_live_records_to_rows'`

- [ ] **Step 3: Add helpers to `extraction/scheduler.py`**

Add near the top of the helpers section (after the `BD_TZ` definition, before `_upsert_macro_df`):

```python
import math


def _to_int(value: object) -> int | None:
    """Coerce to int; None for missing/NaN/unparseable (pandas may emit NaN floats)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return int(f)


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


_LIVE_UPSERT_SQL = """
    INSERT INTO stock_prices
        (time, ticker, open, high, low, close, volume, trades,
         value_bdt, prev_close, change_pct, source, quality_flag)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
    ON CONFLICT (time, ticker) DO UPDATE SET
        high=EXCLUDED.high, low=EXCLUDED.low, close=EXCLUDED.close,
        volume=EXCLUDED.volume, trades=EXCLUDED.trades,
        value_bdt=EXCLUDED.value_bdt, change_pct=EXCLUDED.change_pct,
        prev_close=EXCLUDED.prev_close, source=EXCLUDED.source,
        ingested_at=NOW(), quality_flag=EXCLUDED.quality_flag
"""


def _market_is_open(now: datetime) -> bool:
    """DSE trades Sun–Thu, 10:00–14:30 Asia/Dhaka. `now` must be BD-tz-aware.
    Mon=0..Sun=6; Fri=4, Sat=5 are closed."""
    if now.weekday() in (4, 5):
        return False
    minutes = now.hour * 60 + now.minute
    return 600 <= minutes <= 870


def _live_records_to_rows(records, bucket_time, source):
    """Map live_prices snapshot records to stock_prices INSERT tuples.

    One tuple per ticker. close = ltp (live feeds) or close. Skips rows with no
    ticker or no close. Computes change_pct from prev_close when the feed omits
    it. Falls back value_mn*1e6 → value_bdt. `open` is None (live feeds carry no
    open). quality_flag='live'.
    """
    rows = []
    for rec in records:
        ticker = str(rec.get("ticker") or "").strip()
        if not ticker:
            continue
        close = rec.get("ltp")
        if close is None:
            close = rec.get("close")
        close = _to_float(close)
        if close is None:
            continue

        prev_close = _to_float(rec.get("prev_close"))
        change_pct = _to_float(rec.get("change_pct"))
        if change_pct is None and prev_close not in (None, 0):
            change_pct = (close - prev_close) / prev_close * 100.0

        value_bdt = _to_float(rec.get("value_bdt"))
        if value_bdt is None:
            value_mn = _to_float(rec.get("value_mn"))
            if value_mn is not None:
                value_bdt = value_mn * 1_000_000

        rows.append((
            bucket_time,                    # $1  time (trading-day bucket)
            ticker,                         # $2  ticker
            None,                           # $3  open (not in live feeds)
            _to_float(rec.get("high")),     # $4  high
            _to_float(rec.get("low")),      # $5  low
            close,                          # $6  close (= ltp)
            _to_int(rec.get("volume")),     # $7  volume
            _to_int(rec.get("trades")),     # $8  trades
            value_bdt,                      # $9  value_bdt
            prev_close,                     # $10 prev_close
            change_pct,                     # $11 change_pct
            source,                         # $12 source
            "live",                         # $13 quality_flag
        ))
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_live_prices_ingest.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add extraction/scheduler.py tests/unit/test_live_prices_ingest.py
git commit -m "feat(scheduler): add live_prices row-mapping helper + market guard"
```

---

### Task 2: Implement `job_live_prices()` ingest body (`extraction/scheduler.py`)

Replace the stub body so the job fetches the stream and UPSERTs the rows.

**Files:**
- Modify: `extraction/scheduler.py:75-85` (the `job_live_prices` function)

- [ ] **Step 1: Replace the `job_live_prices` function**

Replace the existing stub:

```python
async def job_live_prices() -> None:
    """Fetch live prices every 15 min during market hours."""
    logger.info("job_live_prices: starting")
    # TODO: implement ingest_live_prices()
    try:
        from mgmt.cache import cache_delete_pattern
        await cache_delete_pattern("cache:pipeline_status:*")
        await cache_delete_pattern("cache:live_prices*")
    except Exception as exc:
        logger.warning("job_live_prices: cache invalidation failed error=%s", exc)
    logger.info("job_live_prices: complete")
```

with:

```python
async def _invalidate_live_caches() -> None:
    """Drop caches that depend on live prices so the next request rebuilds."""
    try:
        from mgmt.cache import cache_delete_pattern
        await cache_delete_pattern("cache:pipeline_status:*")
        await cache_delete_pattern("cache:live_prices*")
        await cache_delete_pattern("cache:live_snapshot")
        await cache_delete_pattern("cache:api:stocks:detail:*")
        await cache_delete_pattern("cache:api:market:*")
    except Exception as exc:
        logger.warning("job_live_prices: cache invalidation failed error=%s", exc)


async def job_live_prices() -> None:
    """Fetch live prices during market hours and UPSERT one row per ticker per
    trading day into stock_prices. Live feeds report cumulative volume/value, so
    appending a fresh row each run would inflate daily_ohlcv's SUM(volume); the
    per-day bucket + ON CONFLICT DO UPDATE keeps a single converging bar."""
    from db.pool import get_pool
    from extraction.base import AllAdaptersFailedError
    from extraction.jobs import job_run
    from extraction.registry import STREAMS

    logger.info("job_live_prices: starting")
    now = datetime.now(BD_TZ)
    if not _market_is_open(now):
        logger.info("job_live_prices: market closed — skipping write")
        await _invalidate_live_caches()
        return

    async with job_run("live_price_pull", stream_name="live_prices") as ctx:
        try:
            result = await STREAMS["live_prices"].fetch()
        except AllAdaptersFailedError as exc:
            logger.error("job_live_prices: all adapters failed error=%s", exc)
            raise

        records = result.data.to_dict("records")
        ctx["records_fetched"] = len(records)

        bucket_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
        rows = _live_records_to_rows(records, bucket_time, result.source_name)

        if rows:
            pool = await get_pool()
            await pool.executemany(_LIVE_UPSERT_SQL, rows)
        ctx["records_inserted"] = len(rows)
        logger.info("job_live_prices: upserted=%d source=%s", len(rows), result.source_name)

    await _invalidate_live_caches()
    logger.info("job_live_prices: complete")
```

- [ ] **Step 2: Verify import of `datetime` already present**

Run: `grep -n "from datetime import" extraction/scheduler.py`
Expected: line 14 already imports `datetime` (`from datetime import datetime, timezone`). No change needed.

- [ ] **Step 3: Verify the existing unit test suite still imports the module**

Run: `pytest tests/unit/test_live_prices_ingest.py -v`
Expected: PASS (module still imports; helpers unchanged)

- [ ] **Step 4: Lint + typecheck the changed file**

Run: `ruff check extraction/scheduler.py && mypy extraction/scheduler.py`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add extraction/scheduler.py
git commit -m "feat(scheduler): implement live_prices ingest with per-day upsert"
```

---

### Task 3: Switch ingest cadence to every 2 minutes (`mgmt/config.py`)

**Files:**
- Modify: `mgmt/config.py:64`

- [ ] **Step 1: Change the cron minute spec**

Replace line 64:

```python
    live_prices_minutes: str = "0,15,30,45"
```

with:

```python
    live_prices_minutes: str = "*/2"
```

- [ ] **Step 2: Verify config loads**

Run: `python -c "from mgmt.config import get_settings; print(get_settings().live_prices_minutes)"`
Expected: `*/2`

- [ ] **Step 3: Commit**

```bash
git add mgmt/config.py
git commit -m "feat(config): run live_prices ingest every 2 min during market hours"
```

---

### Task 4: `LivePrice` response schema (`api/schemas/stocks.py`)

**Files:**
- Modify: `api/schemas/stocks.py`

- [ ] **Step 1: Add the schema**

Append to `api/schemas/stocks.py`:

```python
class LivePrice(BaseModel):
    ticker: str
    available: bool
    ltp: float | None = None
    high: float | None = None
    low: float | None = None
    prev_close: float | None = None
    change_pct: float | None = None
    volume: float | None = None
    value_bdt: float | None = None
    market_status: str
    as_of: datetime
```

- [ ] **Step 2: Verify import**

Run: `python -c "from api.schemas.stocks import LivePrice; print(LivePrice.model_fields.keys())"`
Expected: prints the field names including `ticker`, `available`, `ltp`, `market_status`, `as_of`

- [ ] **Step 3: Commit**

```bash
git add api/schemas/stocks.py
git commit -m "feat(api): add LivePrice response schema"
```

---

### Task 5: `GET /stocks/{ticker}/live` endpoint (`api/routers/stocks.py`)

**Files:**
- Modify: `api/routers/stocks.py`
- Test: `tests/unit/test_api_stocks_live.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_api_stocks_live.py`:

```python
import os
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api import deps


def _make_app():
    from api.routers.stocks import router
    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def _get_user():
        return {"id": 1, "email": "a@b.com", "tier": "free", "is_active": True}

    app.dependency_overrides[deps.get_current_user] = _get_user
    return app


def _pool_company_exists(exists):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=({"?column?": 1} if exists else None))
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def test_live_returns_filtered_ticker():
    app = _make_app()
    app.dependency_overrides[deps.get_db] = lambda: _pool_company_exists(True)
    snapshot = [
        {"ticker": "GP", "ltp": 400.0, "high": 410.0, "low": 395.0,
         "prev_close": 390.0, "change_pct": 2.56, "volume": 1000, "value_bdt": 4e8},
        {"ticker": "CITYBANK", "ltp": 23.4, "change_pct": 1.7},
    ]
    with patch("api.routers.stocks._live_snapshot", AsyncMock(return_value=snapshot)), \
         patch("api.routers.stocks._market_status", return_value="Open"):
        client = TestClient(_make_app_with_db())  # placeholder; replaced below
```

Replace the body of `test_live_returns_filtered_ticker` and add the remaining tests with this complete version (overwrite the whole file with the block below — the snippet above is only to show intent):

```python
import os
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api import deps


def _pool_company_exists(exists):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=({"?column?": 1} if exists else None))
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def _app(pool):
    from api.routers.stocks import router
    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def _get_user():
        return {"id": 1, "email": "a@b.com", "tier": "free", "is_active": True}

    app.dependency_overrides[deps.get_current_user] = _get_user
    app.dependency_overrides[deps.get_db] = lambda: pool
    return app


def test_live_returns_filtered_ticker():
    snapshot = [
        {"ticker": "GP", "ltp": 400.0, "high": 410.0, "low": 395.0,
         "prev_close": 390.0, "change_pct": 2.56, "volume": 1000, "value_bdt": 4e8},
        {"ticker": "CITYBANK", "ltp": 23.4, "change_pct": 1.7},
    ]
    with patch("api.routers.stocks._live_snapshot", AsyncMock(return_value=snapshot)), \
         patch("api.routers.stocks._market_status", return_value="Open"):
        client = TestClient(_app(_pool_company_exists(True)))
        resp = client.get("/api/stocks/GP/live")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticker"] == "GP"
    assert body["available"] is True
    assert body["ltp"] == 400.0
    assert body["market_status"] == "Open"


def test_live_404_for_unknown_ticker():
    with patch("api.routers.stocks._live_snapshot", AsyncMock(return_value=[])), \
         patch("api.routers.stocks._market_status", return_value="Closed"):
        client = TestClient(_app(_pool_company_exists(False)))
        resp = client.get("/api/stocks/NOPE/live")
    assert resp.status_code == 404


def test_live_available_false_when_absent_from_snapshot():
    snapshot = [{"ticker": "GP", "ltp": 400.0}]
    with patch("api.routers.stocks._live_snapshot", AsyncMock(return_value=snapshot)), \
         patch("api.routers.stocks._market_status", return_value="Open"):
        client = TestClient(_app(_pool_company_exists(True)))
        resp = client.get("/api/stocks/CITYBANK/live")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["ltp"] is None
    assert body["ticker"] == "CITYBANK"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_api_stocks_live.py -v`
Expected: FAIL — `AttributeError: <module 'api.routers.stocks'> does not have the attribute '_live_snapshot'`

- [ ] **Step 3: Add the snapshot helper, the `_f` helper, the status import, and the endpoint**

In `api/routers/stocks.py`, update the imports block at the top:

```python
from __future__ import annotations
import math
from datetime import date, datetime, timezone
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from api.deps import get_current_user, get_db
from api.schemas.common import PagedResponse
from api.routers.market import _market_status
from extraction.base import AllAdaptersFailedError
from extraction.registry import STREAMS
from api.schemas.stocks import (
    AnnouncementsResponse, AnnouncementRow, CompanyRow, FundamentalsResponse,
    FundamentalsRow, HealthScoreRow, LatestFundamentals, LatestPrice, LivePrice,
    OHLCVResponse, PredictionRow, PredictionsResponse, StockDetail,
)
```

Add these helpers immediately after the existing `_cache_set` function (around line 33):

```python
def _f(value: Any) -> float | None:
    """JSON-safe float; None for unparseable or non-finite (Starlette uses
    allow_nan=False, so one NaN/inf 500s the whole response)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


async def _live_snapshot() -> list[dict]:
    """All-stock live snapshot from the live_prices stream, cached 75s and shared
    across per-ticker requests. 502 if every adapter in the chain fails."""
    cached = await _cache_get("cache:live_snapshot")
    if cached is not None:
        return cached
    try:
        result = await STREAMS["live_prices"].fetch()
    except AllAdaptersFailedError as exc:
        raise HTTPException(status_code=502, detail=f"live_prices unavailable: {exc}") from exc
    records = result.data.to_dict("records")
    await _cache_set("cache:live_snapshot", records, ttl=75)
    return records
```

Add the endpoint at the end of the file:

```python
@router.get("/{ticker}/live", response_model=LivePrice)
async def get_live_price(ticker: str, pool=Depends(get_db), _user=Depends(get_current_user)):
    ticker = ticker.upper()
    async with pool.acquire() as conn:
        exists = await conn.fetchrow("SELECT 1 FROM companies WHERE ticker = $1", ticker)
    if not exists:
        raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found")

    status = _market_status()
    as_of = datetime.now(timezone.utc)

    for rec in await _live_snapshot():
        if str(rec.get("ticker") or "").strip().upper() != ticker:
            continue
        ltp = rec.get("ltp") if rec.get("ltp") is not None else rec.get("close")
        value = rec.get("value_bdt")
        if value is None and rec.get("value_mn") is not None:
            value = _f(rec.get("value_mn"))
            value = value * 1_000_000 if value is not None else None
        return {
            "ticker": ticker,
            "available": True,
            "ltp": _f(ltp),
            "high": _f(rec.get("high")),
            "low": _f(rec.get("low")),
            "prev_close": _f(rec.get("prev_close")),
            "change_pct": _f(rec.get("change_pct")),
            "volume": _f(rec.get("volume")),
            "value_bdt": _f(value),
            "market_status": status,
            "as_of": as_of,
        }

    return {
        "ticker": ticker,
        "available": False,
        "ltp": None, "high": None, "low": None, "prev_close": None,
        "change_pct": None, "volume": None, "value_bdt": None,
        "market_status": status,
        "as_of": as_of,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_api_stocks_live.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full unit suite + lint to confirm no regressions / no circular import**

Run: `pytest tests/unit/ -q && ruff check api/routers/stocks.py && mypy api/routers/stocks.py`
Expected: all pass. (`api.routers.stocks` importing `api.routers.market._market_status` is safe — `market.py` does not import `stocks.py`.)

- [ ] **Step 6: Commit**

```bash
git add api/routers/stocks.py tests/unit/test_api_stocks_live.py
git commit -m "feat(api): add GET /stocks/{ticker}/live with shared snapshot cache"
```

---

### Task 6: `LivePrice` TS type (`frontend/lib/types.ts`)

**Files:**
- Modify: `frontend/lib/types.ts`

- [ ] **Step 1: Add the interface**

Append to `frontend/lib/types.ts`:

```typescript
export interface LivePrice {
  ticker: string
  available: boolean
  ltp: number | null
  high: number | null
  low: number | null
  prev_close: number | null
  change_pct: number | null
  volume: number | null
  value_bdt: number | null
  market_status: string
  as_of: string
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/lib/types.ts
git commit -m "feat(frontend): add LivePrice type"
```

---

### Task 7: `LivePrice.tsx` client component (`frontend/components/stocks/LivePrice.tsx`)

Renders the header price + change% + HIGH/LOW/VOLUME/VALUE strip, seeded from the SSR `latest_price` for instant paint, then polls `/stocks/{ticker}/live` every 120s while the market is open.

**Files:**
- Create: `frontend/components/stocks/LivePrice.tsx`

- [ ] **Step 1: Create the component**

```tsx
'use client'

import { useEffect, useState } from 'react'
import { get } from '@/lib/api'
import type { LatestPrice, LivePrice as LivePriceType } from '@/lib/types'

function fmtBDT(n: number | null | undefined): string {
  if (n == null) return '—'
  return `৳${Number(n).toFixed(2)}`
}
function fmtCap(bdt: number | null | undefined): string {
  if (!bdt) return '—'
  const cr = Number(bdt) / 10_000_000
  if (cr >= 1000) return `৳${(cr / 1000).toFixed(1)}K Cr`
  return `৳${cr.toFixed(0)} Cr`
}
function fmtVol(v: number | null | undefined): string {
  const n = Number(v)
  if (!n) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

const POLL_MS = 120_000

function seed(initial: LatestPrice | null): LivePriceType {
  if (!initial) {
    return {
      ticker: '', available: false, ltp: null, high: null, low: null,
      prev_close: null, change_pct: null, volume: null, value_bdt: null,
      market_status: 'Closed', as_of: new Date().toISOString(),
    }
  }
  return {
    ticker: '', available: true,
    ltp: initial.close != null ? Number(initial.close) : null,
    high: initial.high != null ? Number(initial.high) : null,
    low: initial.low != null ? Number(initial.low) : null,
    prev_close: null,
    change_pct: initial.change_pct != null ? Number(initial.change_pct) : null,
    volume: initial.volume != null ? Number(initial.volume) : null,
    value_bdt: initial.value_bdt != null ? Number(initial.value_bdt) : null,
    market_status: 'Open',
    as_of: initial.time ?? new Date().toISOString(),
  }
}

export default function LivePrice({
  ticker,
  initial,
}: {
  ticker: string
  initial: LatestPrice | null
}) {
  const [data, setData] = useState<LivePriceType>(() => seed(initial))

  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setInterval> | null = null

    async function tick() {
      try {
        const r = await get<LivePriceType>(`/api/stocks/${ticker}/live`)
        if (!alive) return
        if (r.available) setData(r)
        else setData(d => ({ ...d, market_status: r.market_status, as_of: r.as_of }))
        if (r.market_status !== 'Open' && timer) {
          clearInterval(timer)
          timer = null
        }
      } catch {
        /* keep last known values */
      }
    }

    tick()
    timer = setInterval(tick, POLL_MS)
    return () => {
      alive = false
      if (timer) clearInterval(timer)
    }
  }, [ticker])

  const price = data.ltp
  const changePct = data.change_pct ?? 0
  const isUp = changePct >= 0
  const isLive = data.market_status === 'Open'
  const asOf = new Date(data.as_of).toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
  })

  return (
    <div className="flex flex-wrap items-start justify-between gap-4 w-full">
      <div>
        {/* Price */}
        <div className="flex items-baseline gap-3">
          <span className="text-[42px] font-mono font-bold tabular-nums leading-none text-white">
            {price != null ? `৳${price.toFixed(2)}` : '—'}
          </span>
          {price != null && (
            <span
              className="text-[18px] font-mono font-semibold tabular-nums"
              style={{ color: isUp ? '#00d4a4' : '#ff4d6a' }}
            >
              {isUp ? '+' : ''}
              {changePct.toFixed(2)}%
            </span>
          )}
        </div>
        {/* Live status */}
        <div className="flex items-center gap-1.5 mt-1.5">
          <span
            className={`w-1.5 h-1.5 rounded-full ${isLive ? 'bg-[#00d4a4] animate-pulse' : 'bg-[#6b6b80]'}`}
          />
          <span className="text-[9px] font-mono uppercase tracking-[0.18em] text-[#6b6b80]">
            {isLive ? `live · as of ${asOf}` : 'market closed'}
          </span>
        </div>
      </div>

      {/* OHLV strip */}
      {price != null && (
        <div className="flex gap-5 self-end pb-1">
          {[
            { l: 'HIGH', v: fmtBDT(data.high), c: '#00d4a4' },
            { l: 'LOW', v: fmtBDT(data.low), c: '#ff4d6a' },
            { l: 'VOLUME', v: fmtVol(data.volume), c: null },
            { l: 'VALUE', v: fmtCap(data.value_bdt), c: null },
          ].map(({ l, v, c }) => (
            <div key={l} className="text-center">
              <div className="text-[9px] font-mono uppercase tracking-[0.2em] text-[#6b6b80] mb-0.5">
                {l}
              </div>
              <div
                className="text-sm font-mono font-medium tabular-nums"
                style={{ color: c ?? '#e8e8f0' }}
              >
                {v}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/components/stocks/LivePrice.tsx
git commit -m "feat(frontend): LivePrice client component with 2-min polling"
```

---

### Task 8: Mount `<LivePrice>` in the detail page (`frontend/app/(app)/stocks/[ticker]/page.tsx`)

**Files:**
- Modify: `frontend/app/(app)/stocks/[ticker]/page.tsx`

- [ ] **Step 1: Import the component**

After the existing component imports (around line 7), add:

```tsx
import LivePrice from '@/components/stocks/LivePrice'
```

- [ ] **Step 2: Replace the inline price + OHLV blocks**

Replace this block (the `{/* Price */}` div through the closing of the `{/* OHLV strip */}` block — currently lines ~104-138, i.e. everything from `<div className="flex items-baseline gap-3 mt-2">` down to the `)}` that closes the `{latest_price && ( ... )}` OHLV strip):

```tsx
            {/* Price */}
            <div className="flex items-baseline gap-3 mt-2">
              <span className="text-[42px] font-mono font-bold tabular-nums leading-none text-white">
                {latest_price ? `৳${price.toFixed(2)}` : '—'}
              </span>
              {latest_price && (
                <span
                  className="text-[18px] font-mono font-semibold tabular-nums"
                  style={{ color: isUp ? '#00d4a4' : '#ff4d6a' }}
                >
                  {isUp ? '+' : ''}{changePct.toFixed(2)}%
                </span>
              )}
            </div>
          </div>

          {/* OHLV strip */}
          {latest_price && (
            <div className="flex gap-5 self-end pb-1">
              {[
                { l: 'HIGH',   v: fmtBDT(latest_price.high),   c: '#00d4a4' },
                { l: 'LOW',    v: fmtBDT(latest_price.low),    c: '#ff4d6a' },
                { l: 'VOLUME', v: fmtVol(latest_price.volume), c: null },
                { l: 'VALUE',  v: fmtCap(latest_price.value_bdt != null ? Number(latest_price.value_bdt) * 10_000_000 : null), c: null },
              ].map(({ l, v, c }) => (
                <div key={l} className="text-center">
                  <div className="text-[9px] font-mono uppercase tracking-[0.2em] text-[#6b6b80] mb-0.5">{l}</div>
                  <div className="text-sm font-mono font-medium tabular-nums"
                    style={{ color: c ?? '#e8e8f0' }}>
                    {v}
                  </div>
                </div>
              ))}
            </div>
          )}
```

with:

```tsx
          </div>

          {/* Live price + OHLV — client component polls every 2 min */}
          <LivePrice ticker={company.ticker} initial={latest_price} />
```

Note: the first `</div>` above closes the symbol/name block that previously sat above the price; keep the existing structure so `<LivePrice>` becomes a sibling inside the header card's flex row.

- [ ] **Step 3: Remove now-unused locals**

The `price`, `changePct`, `isUp` consts (lines ~56-58) and the `fmtBDT`/`fmtVol`/`fmtCap` helpers are now only used by `LivePrice` if not referenced elsewhere in the file. Check before deleting:

Run: `grep -nE "\b(price|changePct|isUp|fmtBDT|fmtVol|fmtCap)\b" "frontend/app/(app)/stocks/[ticker]/page.tsx"`

- `fmtBDT`, `fmtCap`, `fmtVol`, `fmtPct`, `fmt` are still used by the `metrics` array → keep them.
- `price`, `changePct`, `isUp` are no longer referenced after the replacement → delete these three lines:

```tsx
  const price = Number(latest_price?.close)
  const changePct = Number(latest_price?.change_pct ?? 0)
  const isUp = changePct >= 0
```

- [ ] **Step 4: Build + lint the frontend**

Run (from `frontend/`): `npm run lint && npm run build`
Expected: lint clean (no unused-var errors for `price`/`changePct`/`isUp`); build succeeds.

- [ ] **Step 5: Commit**

```bash
git add "frontend/app/(app)/stocks/[ticker]/page.tsx"
git commit -m "feat(frontend): live-updating price header on stock detail page"
```

---

### Task 9: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Backend unit suite**

Run: `pytest tests/unit/ -q`
Expected: all pass, including `test_live_prices_ingest.py` and `test_api_stocks_live.py`.

- [ ] **Step 2: Lint + typecheck**

Run: `make check`
Expected: ruff + mypy clean.

- [ ] **Step 3: Frontend build**

Run (from `frontend/`): `npm run build`
Expected: success.

- [ ] **Step 4: Manual smoke (optional, requires running stack during market hours)**

- `make up`, then `GET http://localhost:8000/api/stocks/CITYBANK/live` → 200 with `available` + `market_status`.
- Open the stock detail page → header shows live dot + "as of HH:MM", price refreshes within 2 min while market open.

---

## Self-Review

**Spec coverage:**
- ingest_live_prices (per-day upsert, market guard, cache invalidation) → Tasks 1, 2.
- Cumulative-volume fix (one row/day, DO UPDATE) → Task 1 `_LIVE_UPSERT_SQL` + bucket_time; Task 1 tests assert single row + bucket reuse.
- Schedule every 2 min → Task 3.
- `GET /{ticker}/live` (shared 75s cache, 404 unknown, available:false) → Task 5 + tests.
- `LivePriceResponse` schema → Task 4.
- FE client component, 120s poll, pause when closed, SSR seed → Tasks 6, 7, 8.
- Tests → Tasks 1, 5.

**Placeholder scan:** Task 5 Step 1 intentionally shows an "intent" snippet then a full overwrite block — the full block is complete and is the one to write. No TBD/TODO elsewhere.

**Type consistency:** `_live_records_to_rows(records, bucket_time, source)` tuple order matches `_LIVE_UPSERT_SQL` 13 params (verified field-by-field). `LivePrice` (py) fields == `LivePrice` (ts) fields == endpoint return keys. `_market_status` reused from `market.py`; `_market_is_open` (scheduler) is a separate BD-tz guard. `volume` typed `float | None` end-to-end to avoid NaN→int crashes.
