# Bull vs Bear Market Indicator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Bull vs Bear regime card to the market dashboard, decided by DSEX index value vs its 50-day moving average.

**Architecture:** A new `index_daily` table stores one row per trading day (seeded by a one-time bdshare backfill, kept current by the existing EOD job). A new `GET /market/regime` endpoint computes the regime from that series via a pure, unit-tested function. A `RegimeCard` React component renders it on the dashboard.

**Tech Stack:** Python 3 / FastAPI / asyncpg / PostgreSQL+TimescaleDB / pandas / bdshare (backend); Next.js 16 / React 19 / TypeScript / Jest (frontend).

Spec: `docs/superpowers/specs/2026-06-01-bull-bear-indicator-design.md`

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `db/migrations/029_index_daily.sql` | Create | `index_daily` table |
| `api/schemas/market.py` | Modify | `MarketRegime` response model |
| `api/routers/market.py` | Modify | `compute_regime()` pure fn + `/market/regime` endpoint |
| `tests/unit/test_market_regime.py` | Create | unit tests for `compute_regime` |
| `extraction/bulk_load/index_history_loader.py` | Create | one-time 30-day backfill |
| `extraction/scheduler.py` | Modify | `_persist_index_snapshot()` + call from `job_eod_snapshot` |
| `Makefile` | Modify | `backfill-index-history` target |
| `frontend/lib/types.ts` | Modify | `MarketRegime` interface |
| `frontend/lib/server-api.ts` | Modify | `market.regime()` client method |
| `frontend/components/market/RegimeCard.tsx` | Create | regime card UI |
| `frontend/__tests__/components/market/RegimeCard.test.tsx` | Create | component tests |
| `frontend/app/(app)/dashboard/page.tsx` | Modify | fetch + render `RegimeCard` |

---

## Task 1: Create the `index_daily` table

**Files:**
- Create: `db/migrations/029_index_daily.sql`

Migrations are plain SQL applied in filename order by `db/migrate.py` (idempotent, tracked in `_migrations`).

- [ ] **Step 1: Write the migration**

Create `db/migrations/029_index_daily.sql`:

```sql
-- Daily snapshot of the three DSE indices (DSEX, DS30, DSES), one row per
-- trading day. Backs the Bull/Bear regime indicator: regime = DSEX vs its
-- 50-day moving average. No index history existed before this table; it is
-- seeded by a one-time bdshare backfill (extraction/bulk_load/index_history_loader.py)
-- and kept current by job_eod_snapshot (extraction/scheduler.py).

CREATE TABLE IF NOT EXISTS index_daily (
    date        date PRIMARY KEY,
    dsex        numeric,
    ds30        numeric,
    dses        numeric,
    source      text NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now()
);
```

- [ ] **Step 2: Apply the migration**

Run: `make migrate-local`
Expected: output lists `029_index_daily.sql` as applied (re-runs are no-ops).

- [ ] **Step 3: Verify the table exists**

Run: `make db-shell` then `\d index_daily`
Expected: table shows columns `date, dsex, ds30, dses, source, ingested_at` with `date` as primary key. Type `\q` to exit.

- [ ] **Step 4: Commit**

```bash
git add db/migrations/029_index_daily.sql
git commit -m "feat(db): add index_daily table for DSEX history"
```

---

## Task 2: `compute_regime()` pure function + tests

**Files:**
- Modify: `api/routers/market.py`
- Test: `tests/unit/test_market_regime.py`

`compute_regime` is a pure function (no DB) so it is unit-testable in isolation, mirroring how `_market_status` is tested in `tests/unit/test_api_market_indices.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_market_regime.py`:

```python
# tests/unit/test_market_regime.py
import os
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

import pytest

from api.routers.market import compute_regime


def test_bull_when_dsex_at_or_above_ma():
    # most-recent-first; latest 5500 is above the mean of the series
    series = [5500.0] + [5000.0] * 49
    r = compute_regime(series, as_of="2026-06-01")
    assert r["regime"] == "Bull"
    assert r["window"] == 50
    assert r["provisional"] is False
    assert r["data_status"] == "ok"
    assert r["dsex"] == pytest.approx(5500.0)
    assert r["ma"] == pytest.approx((5500.0 + 5000.0 * 49) / 50)
    assert r["distance_pct"] > 0
    assert r["as_of"] == "2026-06-01"


def test_bear_when_dsex_below_ma():
    series = [4800.0] + [5000.0] * 49
    r = compute_regime(series, as_of="2026-06-01")
    assert r["regime"] == "Bear"
    assert r["distance_pct"] < 0
    assert r["data_status"] == "ok"


def test_provisional_when_window_below_50():
    series = [5100.0] + [5000.0] * 31  # 32 days
    r = compute_regime(series, as_of="2026-06-01")
    assert r["window"] == 32
    assert r["provisional"] is True
    assert r["data_status"] == "provisional"
    assert r["regime"] == "Bull"


def test_insufficient_when_fewer_than_five_days():
    series = [5000.0, 5010.0, 4990.0]  # 3 days
    r = compute_regime(series, as_of="2026-06-01")
    assert r["regime"] == "Unknown"
    assert r["data_status"] == "insufficient"
    assert r["ma"] is None
    assert r["window"] == 3
    assert r["dsex"] == pytest.approx(5000.0)


def test_empty_series():
    r = compute_regime([], as_of=None)
    assert r["regime"] == "Unknown"
    assert r["data_status"] == "insufficient"
    assert r["dsex"] is None
    assert r["ma"] is None
    assert r["window"] == 0
    assert r["as_of"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_market_regime.py -v`
Expected: FAIL with `ImportError: cannot import name 'compute_regime'`.

- [ ] **Step 3: Implement `compute_regime`**

In `api/routers/market.py`, add after the `_market_status` function (around line 109):

```python
_MA_WINDOW = 50   # target moving-average window (trading days)
_MIN_DAYS = 5     # below this, refuse to call a regime


def compute_regime(dsex_series: list[float], as_of: str | None) -> dict:
    """Bull/Bear regime from a DSEX series ordered most-recent-first.

    Bull when the latest DSEX value is >= the mean of the last `window` values
    (window = min(50, len)). Marked provisional while the window is short, and
    Unknown/insufficient below _MIN_DAYS so a thin series never forces a call.
    """
    n = len(dsex_series)
    if n < _MIN_DAYS:
        return {
            "regime": "Unknown",
            "dsex": dsex_series[0] if n else None,
            "ma": None,
            "window": n,
            "provisional": True,
            "distance_pct": None,
            "as_of": as_of,
            "data_status": "insufficient",
        }
    window = min(_MA_WINDOW, n)
    dsex = dsex_series[0]
    ma = sum(dsex_series[:window]) / window
    provisional = window < _MA_WINDOW
    distance_pct = (dsex - ma) / ma * 100.0 if ma else None
    return {
        "regime": "Bull" if dsex >= ma else "Bear",
        "dsex": dsex,
        "ma": ma,
        "window": window,
        "provisional": provisional,
        "distance_pct": distance_pct,
        "as_of": as_of,
        "data_status": "provisional" if provisional else "ok",
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_market_regime.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add api/routers/market.py tests/unit/test_market_regime.py
git commit -m "feat(market): add compute_regime bull/bear calculation"
```

---

## Task 3: `MarketRegime` response schema

**Files:**
- Modify: `api/schemas/market.py`

- [ ] **Step 1: Add the schema**

In `api/schemas/market.py`, append after `MarketIndices`:

```python
class MarketRegime(BaseModel):
    regime: str                  # "Bull" | "Bear" | "Unknown"
    dsex: float | None
    ma: float | None
    window: int
    provisional: bool
    distance_pct: float | None
    as_of: str | None
    data_status: str             # "ok" | "provisional" | "insufficient"
```

- [ ] **Step 2: Verify it imports**

Run: `python -c "from api.schemas.market import MarketRegime; print(MarketRegime.model_fields.keys())"`
Expected: prints the field names including `regime`, `data_status`.

- [ ] **Step 3: Commit**

```bash
git add api/schemas/market.py
git commit -m "feat(market): add MarketRegime response schema"
```

---

## Task 4: `GET /market/regime` endpoint

**Files:**
- Modify: `api/routers/market.py`

The endpoint loads the DSEX series from `index_daily` and delegates to `compute_regime`. It always returns 200 (derived/cached data — never 502) and caches for 300s, matching the daily cadence.

- [ ] **Step 1: Add the import**

In `api/routers/market.py`, update the schema import (currently line 14):

```python
from api.schemas.market import HeatmapItem, MarketIndices, MarketRegime, MarketSummary
```

- [ ] **Step 2: Add the endpoint**

In `api/routers/market.py`, add after the `market_indices` endpoint (after line 119, before `_build_indices`):

```python
@router.get("/regime", response_model=MarketRegime)
async def market_regime(pool=Depends(get_db), _user=Depends(get_current_user)):
    cache_key = "cache:api:market:regime"
    cached = await _cache_get(cache_key)
    if cached:
        return cached

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT date, dsex FROM index_daily
            WHERE dsex IS NOT NULL
            ORDER BY date DESC
            LIMIT $1
            """,
            _MA_WINDOW,
        )

    series = [float(r["dsex"]) for r in rows]
    as_of = rows[0]["date"].isoformat() if rows else None
    result = compute_regime(series, as_of)
    await _cache_set(cache_key, result, ttl=300)
    return result
```

- [ ] **Step 3: Verify the route registers**

Run: `python -c "from api.routers.market import router; print([r.path for r in router.routes])"`
Expected: list includes `/market/regime`.

- [ ] **Step 4: Run the full market test module to confirm nothing broke**

Run: `pytest tests/unit/test_api_market_indices.py tests/unit/test_market_regime.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add api/routers/market.py
git commit -m "feat(market): add GET /market/regime endpoint"
```

---

## Task 5: One-time bdshare backfill loader

**Files:**
- Create: `extraction/bulk_load/index_history_loader.py`
- Modify: `Makefile`

`bd.get_market_info()` returns ~30 days of history with `DSEX Index` / `DS30 Index` / `DSES Index` columns per `Date`. This seeds `index_daily` so the MA works on day one. `ON CONFLICT (date) DO NOTHING` makes re-runs safe.

- [ ] **Step 1: Write the loader**

Create `extraction/bulk_load/index_history_loader.py`:

```python
"""
One-time backfill of DSEX/DS30/DSES daily history into index_daily.

Pulls the ~30-day history bdshare's get_market_info() already returns and seeds
index_daily so the /market/regime moving-average is usable on day one.

Usage:
    python -m extraction.bulk_load.index_history_loader
"""
from __future__ import annotations

import asyncio
import logging

import pandas as pd

from db.pool import get_pool
from extraction.normalizers import to_decimal

logger = logging.getLogger(__name__)

# bdshare columns after .strip().upper() (see extraction/adapters/bdshare/market_info.py)
_COL = {"dsex": "DSEX INDEX", "ds30": "DS30 INDEX", "dses": "DSES INDEX"}


async def backfill_index_history() -> dict:
    try:
        import bdshare as bd
    except ImportError:
        logger.error("index_backfill: bdshare not installed")
        return {"inserted": 0, "rows": 0}

    raw = bd.get_market_info()
    if raw is None or len(raw) == 0:
        logger.warning("index_backfill: get_market_info returned empty")
        return {"inserted": 0, "rows": 0}

    df = raw.copy()
    df.columns = [c.strip().upper() for c in df.columns]

    pool = await get_pool()
    inserted = 0
    for _, row in df.iterrows():
        try:
            d = pd.to_datetime(row.get("DATE")).date()
        except (ValueError, TypeError):
            logger.warning("index_backfill: unparseable DATE=%r — skipping", row.get("DATE"))
            continue
        rec = await pool.fetchrow(
            """
            INSERT INTO index_daily (date, dsex, ds30, dses, source)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (date) DO NOTHING
            RETURNING date
            """,
            d,
            to_decimal(row.get(_COL["dsex"])),
            to_decimal(row.get(_COL["ds30"])),
            to_decimal(row.get(_COL["dses"])),
            "bdshare_backfill",
        )
        if rec:
            inserted += 1

    logger.info("index_backfill_done inserted=%d rows=%d", inserted, len(df))
    return {"inserted": inserted, "rows": len(df)}


if __name__ == "__main__":
    logging.basicConfig(level="INFO", format="%(levelname)s %(message)s")
    asyncio.run(backfill_index_history())
```

- [ ] **Step 2: Add the Makefile target**

In `Makefile`, under the "Bulk data load (one-time)" section (after the `load-historical` target, around line 111):

```make
backfill-index-history: ## Seed index_daily with ~30d DSEX/DS30/DSES history from bdshare
	$(PYTHON) -m extraction.bulk_load.index_history_loader
```

- [ ] **Step 3: Verify the module imports (offline, no bdshare call)**

Run: `python -c "from extraction.bulk_load.index_history_loader import backfill_index_history; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Run the backfill (requires DB up + bdshare reachable)**

Run: `make backfill-index-history`
Expected: log line `index_backfill_done inserted=N rows=N` with N ≈ 30. (If bdshare is unreachable, this is expected to log empty and insert 0 — re-run later; not a code failure.)

- [ ] **Step 5: Commit**

```bash
git add extraction/bulk_load/index_history_loader.py Makefile
git commit -m "feat(extraction): bdshare backfill for index_daily history"
```

---

## Task 6: Daily snapshot in `job_eod_snapshot`

**Files:**
- Modify: `extraction/scheduler.py`

`job_eod_snapshot` already runs once at close on trading days (and on a compressed interval in test mode via `test_eod_snapshot_minutes`). Add an index-snapshot helper and call it. `ON CONFLICT (date) DO UPDATE` keeps repeated same-day runs converging on one row.

- [ ] **Step 1: Add the helper**

In `extraction/scheduler.py`, add in the "Job Functions" section, immediately before `async def job_eod_snapshot()` (before line 234):

```python
async def _persist_index_snapshot() -> None:
    """Upsert today's DSEX/DS30/DSES values into index_daily (one row per trading
    day). Source: the market_indices stream. Non-fatal — logs and returns on any
    failure so the EOD job continues; the next trading day recovers."""
    from db.pool import get_pool
    from extraction.registry import STREAMS

    try:
        result = await STREAMS["market_indices"].fetch()
    except Exception as exc:
        logger.warning("index_snapshot: fetch failed error=%s", exc)
        return

    idx = {r["index_name"]: r for r in result.data.to_dict("records")}

    def _val(name: str) -> float | None:
        row = idx.get(name)
        return _to_float(row["value"]) if row else None

    today = datetime.now(BD_TZ).date()
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO index_daily (date, dsex, ds30, dses, source)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (date) DO UPDATE SET
            dsex = EXCLUDED.dsex, ds30 = EXCLUDED.ds30, dses = EXCLUDED.dses,
            source = EXCLUDED.source, ingested_at = now()
        """,
        today, _val("DSEX"), _val("DS30"), _val("DSES"), result.source_name,
    )
    logger.info("index_snapshot: upserted date=%s dsex=%s", today, _val("DSEX"))
```

- [ ] **Step 2: Call it from `job_eod_snapshot`**

In `extraction/scheduler.py`, modify `job_eod_snapshot` (lines 234-242) to call the helper first:

```python
async def job_eod_snapshot() -> None:
    """End-of-day snapshot and calculations."""
    logger.info("job_eod_snapshot: starting")
    await _persist_index_snapshot()
    # TODO: implement ingest_eod_snapshot()
    # TODO: update_52week_ranges()
    # TODO: update_circuit_breakers()
    # TODO: update_market_pe()
    # TODO: enqueue celery task: run_ml_inference
    logger.info("job_eod_snapshot: complete")
```

- [ ] **Step 3: Verify the module imports**

Run: `python -c "from extraction.scheduler import _persist_index_snapshot, job_eod_snapshot; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git add extraction/scheduler.py
git commit -m "feat(scheduler): persist daily index snapshot in eod job"
```

---

## Task 7: Frontend type + API client method

**Files:**
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/lib/server-api.ts`

- [ ] **Step 1: Add the type**

In `frontend/lib/types.ts`, add after the `MarketSummary` interface (after line 35):

```typescript
export interface MarketRegime {
  regime: 'Bull' | 'Bear' | 'Unknown'
  dsex: number | null
  ma: number | null
  window: number
  provisional: boolean
  distance_pct: number | null
  as_of: string | null
  data_status: 'ok' | 'provisional' | 'insufficient'
}
```

- [ ] **Step 2: Add the client method**

In `frontend/lib/server-api.ts`, update the type import (line 3-7) to include `MarketRegime`:

```typescript
import type {
  MarketIndices, MarketRegime, MarketMovers, MarketSummary, HeatmapItem,
  StockDetail, FundamentalsResponse, PagedResponse, StockListItem,
  AnnouncementsResponse,
} from './types'
```

Then add the `regime` method to the `market` object (after the `indices` line, line 27):

```typescript
    regime: () => serverGet<MarketRegime>('/api/market/regime'),
```

- [ ] **Step 3: Verify the frontend type-checks**

Run (from `frontend/`): `npm run build`
Expected: build succeeds (no TS errors). Alternatively `npx tsc --noEmit`.

- [ ] **Step 4: Commit**

```bash
git add frontend/lib/types.ts frontend/lib/server-api.ts
git commit -m "feat(frontend): add MarketRegime type and api client"
```

---

## Task 8: `RegimeCard` component + tests

**Files:**
- Create: `frontend/components/market/RegimeCard.tsx`
- Test: `frontend/__tests__/components/market/RegimeCard.test.tsx`

Styling mirrors `IndexCard` (`bg-surface border border-border-custom rounded-lg px-5 py-4 min-w-[148px]`, `text-accent-green` / `text-accent-red` / `text-muted`).

- [ ] **Step 1: Write the failing tests**

Create `frontend/__tests__/components/market/RegimeCard.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import RegimeCard from '../../../components/market/RegimeCard'
import type { MarketRegime } from '../../../lib/types'

const base: MarketRegime = {
  regime: 'Bull', dsex: 5330.89, ma: 5200.0, window: 50,
  provisional: false, distance_pct: 2.52, as_of: '2026-06-01', data_status: 'ok',
}

describe('RegimeCard', () => {
  it('renders Bull with green accent and positive distance', () => {
    render(<RegimeCard data={base} />)
    const regime = screen.getByText('Bull')
    expect(regime).toBeInTheDocument()
    expect(regime).toHaveClass('text-accent-green')
    expect(screen.getByText(/\+2\.52%/)).toBeInTheDocument()
  })

  it('renders Bear with red accent', () => {
    render(<RegimeCard data={{ ...base, regime: 'Bear', dsex: 5000, ma: 5200, distance_pct: -3.85 }} />)
    const regime = screen.getByText('Bear')
    expect(regime).toHaveClass('text-accent-red')
  })

  it('shows provisional window label when provisional', () => {
    render(<RegimeCard data={{ ...base, provisional: true, window: 32, data_status: 'provisional' }} />)
    expect(screen.getByText('provisional (32/50d)')).toBeInTheDocument()
  })

  it('shows collecting-data state when insufficient', () => {
    render(<RegimeCard data={{ ...base, regime: 'Unknown', ma: null, window: 3, data_status: 'insufficient', distance_pct: null }} />)
    expect(screen.getByText('Collecting data')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx jest RegimeCard`
Expected: FAIL — cannot find module `RegimeCard`.

- [ ] **Step 3: Write the component**

Create `frontend/components/market/RegimeCard.tsx`:

```tsx
import type { MarketRegime } from '@/lib/types'

export default function RegimeCard({ data }: { data: MarketRegime }) {
  const insufficient = data.data_status === 'insufficient'
  const isBull = data.regime === 'Bull'
  const accent = insufficient
    ? 'text-muted'
    : isBull
      ? 'text-accent-green'
      : 'text-accent-red'

  return (
    <div className="bg-surface border border-border-custom rounded-lg px-5 py-4 min-w-[148px]">
      <p className="text-xs text-muted uppercase tracking-widest">Market Regime</p>
      <p className={`text-xl font-semibold mt-1 ${accent}`}>
        {insufficient ? 'Collecting data' : data.regime}
      </p>
      {!insufficient && data.dsex != null && data.ma != null && (
        <p className="text-sm text-muted mt-0.5">
          DSEX {data.dsex.toFixed(2)} vs MA {data.ma.toFixed(2)}
          {data.distance_pct != null && (
            <span className={`ml-1 ${accent}`}>
              ({data.distance_pct >= 0 ? '+' : ''}{data.distance_pct.toFixed(2)}%)
            </span>
          )}
        </p>
      )}
      {data.provisional && !insufficient && (
        <p className="text-xs text-muted mt-0.5">provisional ({data.window}/50d)</p>
      )}
      {insufficient && (
        <p className="text-xs text-muted mt-0.5">{data.window}/50d collected</p>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx jest RegimeCard`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/market/RegimeCard.tsx frontend/__tests__/components/market/RegimeCard.test.tsx
git commit -m "feat(frontend): add RegimeCard component"
```

---

## Task 9: Wire `RegimeCard` into the dashboard

**Files:**
- Modify: `frontend/app/(app)/dashboard/page.tsx`

The card joins the existing index-card row. It uses the same `Promise.allSettled` null-tolerant pattern: if the regime fetch rejects, the card is simply omitted.

- [ ] **Step 1: Import and fetch**

In `frontend/app/(app)/dashboard/page.tsx`, add the import after the `HeatmapGrid` import (line 4):

```tsx
import RegimeCard from '@/components/market/RegimeCard'
```

Replace the `Promise.allSettled` block (lines 7-15) with:

```tsx
  const [indices, regime, movers, heatmap] = await Promise.allSettled([
    serverApi.market.indices(),
    serverApi.market.regime(),
    serverApi.market.movers(),
    serverApi.market.heatmap(),
  ])

  const idx = indices.status === 'fulfilled' ? indices.value : null
  const rg  = regime.status  === 'fulfilled' ? regime.value  : null
  const mv  = movers.status  === 'fulfilled' ? movers.value  : null
  const hm  = heatmap.status === 'fulfilled' ? heatmap.value : []
```

- [ ] **Step 2: Render the card in the index row**

In the same file, inside the `idx ?` block, add `<RegimeCard>` after the Advance/Decline card's closing `</div>` and before the closing `</div>` of the `flex flex-wrap` container (after line 43):

```tsx
          {rg && <RegimeCard data={rg} />}
```

The index row becomes (for reference):

```tsx
        <div className="flex flex-wrap gap-4 mb-6">
          <IndexCard label="DSEX" value={idx.dsex_value} changePct={idx.dsex_change_pct} />
          <IndexCard label="DS30" value={idx.ds30_value} changePct={idx.ds30_change_pct} />
          <IndexCard label="DSES" value={idx.dses_value} changePct={idx.dses_change_pct} />
          <div className="bg-surface border border-border-custom rounded-lg px-5 py-4 min-w-[148px]">
            <p className="text-xs text-muted uppercase tracking-widest">Advance / Decline</p>
            <p className="text-xl font-semibold text-white mt-1">
              <span className="text-accent-green">{idx.advance}</span>
              <span className="text-muted mx-1">/</span>
              <span className="text-accent-red">{idx.decline}</span>
            </p>
            <p className="text-xs text-muted mt-0.5">{idx.unchanged} unchanged</p>
          </div>
          {rg && <RegimeCard data={rg} />}
        </div>
```

- [ ] **Step 3: Verify build + existing tests**

Run (from `frontend/`): `npm run build && npm run test`
Expected: build succeeds; jest suite passes (including the new RegimeCard tests).

- [ ] **Step 4: Commit**

```bash
git add "frontend/app/(app)/dashboard/page.tsx"
git commit -m "feat(frontend): show RegimeCard on market dashboard"
```

---

## Final Verification

- [ ] Backend tests: `pytest tests/unit/test_market_regime.py tests/unit/test_api_market_indices.py -v` → all pass.
- [ ] Lint/type: `make check` → clean (or no new findings).
- [ ] Frontend: `cd frontend && npm run build && npm run test` → pass.
- [ ] Migration applied: `make migrate-local` then `\d index_daily` in `make db-shell`.
- [ ] Backfill run once: `make backfill-index-history` → ~30 rows inserted.
- [ ] Manual smoke (DB + api up): `GET /api/market/regime` returns a JSON body with `regime` in {Bull, Bear, Unknown} and a `data_status`.
- [ ] Dashboard renders the "Market Regime" card alongside the index cards.

## Notes on rollout order

1. Apply migration (Task 1) **before** running the backfill (Task 5 Step 4) or the EOD snapshot (Task 6) — both write to `index_daily`.
2. The 50-day MA is **provisional** until ~50 trading days accumulate (≈10 weeks). Until then the card shows `provisional (N/50d)` and still gives a Bull/Bear call from the available window. Below 5 days it shows "Collecting data".
