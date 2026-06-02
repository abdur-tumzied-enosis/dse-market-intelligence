# Phase C — Stock Pages + Sectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close all remaining Phase C checklist gaps — tier-gate the stock APIs, enrich the screener with PE/rating filters, wire PaywallOverlay onto pro-only sections, and build the missing `/sectors` page (sector PE table + sector-level treemap).

**Architecture:** FastAPI consumer API (`api/`) on :8000 + Next.js 16 frontend (`frontend/`) on :3000. Backend enriches the stock-list query and tier-gates fundamentals depth. Frontend `/sectors` is an SSR Server Component; the new `SectorHeatmap` reuses the squarify treemap logic already proven in `HeatmapGrid.tsx`. Tier is read on the client/server by base64-decoding the `tier` claim already present in the access-token JWT.

**Tech Stack:** FastAPI, asyncpg, Pydantic v2, pytest (backend) · Next.js 16.2.6 App Router, TypeScript, Tailwind v4, Jest + React Testing Library (frontend).

---

## Audit: current Phase C state

| Spec checklist item | State | This plan |
|---|---|---|
| `GET /api/stocks`, `/api/stocks/{ticker}` (tier-gated) | endpoints exist; list returns only ticker/name/sector/category/cap; **no PE/rating/price, no tier gating** | Task 1, 2 |
| `/stocks` screener: DataTable + sector/PE/rating filters | page has sector + search + sort only | Task 5 |
| `/stocks/[ticker]`: PriceChart + HealthGauge + RatingBadge | **DONE** (`stocks/[ticker]/page.tsx`) | — |
| FundamentalsChart (3yr free / 10yr pro) | component exists; backend returns 15 rows to everyone, no gating | Task 2, 6 |
| PaywallOverlay on pro-only sections | component exists, **wired nowhere** | Task 6 |
| `/sectors` page: sector PE table + heatmap | **MISSING entirely** (Sidebar already links `/sectors` → 404s) | Task 3, 4, 7, 8 |

**Explicitly out of scope (Phase E, not C):** pro-gating `/api/stocks/{ticker}/predictions`. Leave the predictions endpoint untouched.

**Key facts the implementer needs:**
- `require_tier(*tiers)` dependency already exists in `api/deps.py:37` but is unused. `get_current_user` returns a dict containing `user["tier"]` (a `str`, `"free"` or `"pro"`).
- Access-token JWT (`api/auth/jwt.py:9`) carries a `tier` claim. Frontend stores it in the `dse_access_token` cookie (`frontend/lib/auth.ts`).
- `sector_pe` table columns: `sector, pe, change_pct, market_cap_bdt, fetched_at` (+ id, source, ingested_at). `GET /api/sectors` and `GET /api/sectors/{sector}` already work (`api/routers/sectors.py`).
- Rating thresholds (must stay identical to `frontend/components/stocks/RatingBadge.tsx`): `>=80 STRONG_BUY`, `>=60 BUY`, `>=40 HOLD`, `>=20 SELL`, else `STRONG_SELL`, `null → N/A`.
- Backend test pattern: see `tests/unit/test_api_market.py` — helpers `_pool_with_fetch`, `_make_app([router])`, and `patch("api.routers.X._cache_get"/"._cache_set")`.
- Frontend test pattern: Jest + RTL, mirror `frontend/__tests__/components/market/HeatmapGrid.test.tsx` and `frontend/__tests__/lib/server-api.test.ts`.

**MANDATORY for every frontend task:** `frontend/AGENTS.md` warns this is Next.js 16 with breaking changes. Before writing any frontend code, read the relevant guide under `frontend/node_modules/next/dist/docs/` (App Router, Server Components, `cookies()`). Heed deprecation notices.

---

## Task 1: Enrich `list_stocks` with PE, health score, rating, and latest price

The screener can't filter by PE or rating because the list endpoint doesn't return them. Add them via LATERAL subqueries and compute `rating` server-side.

**Files:**
- Modify: `api/schemas/stocks.py:10-16` (`CompanyRow`)
- Modify: `api/routers/stocks.py:79-121` (`list_stocks`)
- Test: `tests/unit/test_api_stocks.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_api_stocks.py`:

```python
from __future__ import annotations
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_current_user, get_db


def _pool_with(rows, total):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)
    conn.fetchval = AsyncMock(return_value=total)
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=conn)
    acquire.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire)
    return pool


def _client(pool, tier="free"):
    from api.routers.stocks import router
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: pool
    app.dependency_overrides[get_current_user] = lambda: {
        "id": 1, "email": "a@b.c", "tier": tier, "is_active": True,
    }
    return TestClient(app)


def test_list_stocks_includes_pe_health_and_rating():
    rows = [{
        "ticker": "GP", "name": "Grameenphone", "sector": "Telecom",
        "category": "A", "market_cap_bdt": Decimal("1000"), "is_active": True,
        "pe": Decimal("12.5"), "health_score": Decimal("82"),
        "last_close": Decimal("300.5"), "change_pct": Decimal("1.2"),
    }]
    pool = _pool_with(rows, total=1)
    with patch("api.routers.stocks._cache_get", new=AsyncMock(return_value=None)), \
         patch("api.routers.stocks._cache_set", new=AsyncMock()):
        resp = _client(pool).get("/api/stocks?limit=50&offset=0")
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["ticker"] == "GP"
    assert item["pe"] == "12.5"
    assert item["health_score"] == "82"
    assert item["rating"] == "STRONG_BUY"  # health 82 >= 80


def test_list_stocks_rating_null_when_no_score():
    rows = [{
        "ticker": "XX", "name": "X Co", "sector": "Misc",
        "category": None, "market_cap_bdt": None, "is_active": True,
        "pe": None, "health_score": None, "last_close": None, "change_pct": None,
    }]
    pool = _pool_with(rows, total=1)
    with patch("api.routers.stocks._cache_get", new=AsyncMock(return_value=None)), \
         patch("api.routers.stocks._cache_set", new=AsyncMock()):
        resp = _client(pool).get("/api/stocks")
    assert resp.json()["items"][0]["rating"] == "N/A"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_api_stocks.py -v`
Expected: FAIL — `CompanyRow` has no `pe`/`rating` fields → validation error or `KeyError`.

- [ ] **Step 3: Update the `CompanyRow` schema**

In `api/schemas/stocks.py`, replace the `CompanyRow` class (lines 10-16) with:

```python
class CompanyRow(BaseModel):
    ticker: str
    name: str
    sector: str
    category: str | None
    market_cap_bdt: Decimal | None
    is_active: bool
    pe: Decimal | None = None
    health_score: Decimal | None = None
    last_close: Decimal | None = None
    change_pct: Decimal | None = None
    rating: str = "N/A"
```

> Note: `StockDetail.company` reuses `CompanyRow`; the new fields are optional with defaults, so the detail endpoint stays valid without changes.

- [ ] **Step 4: Add a rating helper and enrich the query in `list_stocks`**

In `api/routers/stocks.py`, add this helper near the top (after the existing `_f` helper around line 52):

```python
def _rating(score: Any) -> str:
    if score is None:
        return "N/A"
    s = float(score)
    if s >= 80:
        return "STRONG_BUY"
    if s >= 60:
        return "BUY"
    if s >= 40:
        return "HOLD"
    if s >= 20:
        return "SELL"
    return "STRONG_SELL"
```

Replace the SQL + result block in `list_stocks` (lines 102-121) with:

```python
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT c.ticker, c.name, c.sector, c.category, c.market_cap_bdt, c.is_active,
                   f.pe,
                   s.health_score,
                   p.close AS last_close,
                   p.change_pct
            FROM companies c
            LEFT JOIN LATERAL (
                SELECT pe FROM fundamentals WHERE ticker = c.ticker
                ORDER BY fetched_at DESC LIMIT 1
            ) f ON true
            LEFT JOIN LATERAL (
                SELECT health_score FROM stock_scores WHERE ticker = c.ticker
                ORDER BY scored_at DESC LIMIT 1
            ) s ON true
            LEFT JOIN LATERAL (
                SELECT close, change_pct FROM stock_prices WHERE ticker = c.ticker
                ORDER BY time DESC LIMIT 1
            ) p ON true
            {where.replace("is_active", "c.is_active").replace("sector", "c.sector").replace("category", "c.category")}
            ORDER BY c.market_cap_bdt DESC NULLS LAST
            LIMIT {limit} OFFSET {offset}
            """,
            *params,
        )
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM companies {where}", *params
        )

    items = []
    for r in rows:
        d = dict(r)
        d["rating"] = _rating(d.get("health_score"))
        items.append(d)
    result = {"items": items, "total": total, "limit": limit, "offset": offset}
```

> The `where` string is built earlier (lines 93-100) against bare column names; the `.replace(...)` calls qualify them for the joined `companies c` alias. The `COUNT(*)` query keeps the unqualified `where` since it has no join.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_api_stocks.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Lint + typecheck**

Run: `make lint && make typecheck`
Expected: no new errors.

- [ ] **Step 7: Commit**

```bash
git add api/schemas/stocks.py api/routers/stocks.py tests/unit/test_api_stocks.py
git commit -m "feat(api): enrich stock list with pe, health score, rating, latest price"
```

---

## Task 2: Tier-gate fundamentals depth (free = 3 fiscal years, pro = 10)

`get_fundamentals` returns up to 15 rows to everyone. Cap by tier and signal truncation so the frontend can show a paywall.

**Files:**
- Modify: `api/schemas/stocks.py:82-84` (`FundamentalsResponse`)
- Modify: `api/routers/stocks.py:315-340` (`get_fundamentals`)
- Test: `tests/unit/test_api_stocks.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_api_stocks.py`:

```python
def _fund_rows(n):
    return [{
        "fiscal_year": 2020 - i, "eps": Decimal("1"), "nav": Decimal("10"),
        "pe": Decimal("12"), "cash_div_pct": None, "stock_div_pct": None,
        "sponsor_pct": None, "public_pct": None,
        "fetched_at": __import__("datetime").datetime(2026, 1, 1),
    } for i in range(n)]


def test_fundamentals_free_tier_capped_at_3_years():
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"?column?": 1})   # company exists
    conn.fetch = AsyncMock(return_value=_fund_rows(3))         # query already LIMIT-ed
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=conn)
    acquire.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock(); pool.acquire = MagicMock(return_value=acquire)
    with patch("api.routers.stocks._cache_get", new=AsyncMock(return_value=None)), \
         patch("api.routers.stocks._cache_set", new=AsyncMock()):
        resp = _client(pool, tier="free").get("/api/stocks/GP/fundamentals")
    body = resp.json()
    assert len(body["items"]) == 3
    assert body["max_years"] == 3
    assert body["is_truncated"] is True
    # confirm the LIMIT passed to SQL was 3, not 10
    assert conn.fetch.await_args.args[-1] == 3


def test_fundamentals_pro_tier_allows_10_years():
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"?column?": 1})
    conn.fetch = AsyncMock(return_value=_fund_rows(10))
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=conn)
    acquire.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock(); pool.acquire = MagicMock(return_value=acquire)
    with patch("api.routers.stocks._cache_get", new=AsyncMock(return_value=None)), \
         patch("api.routers.stocks._cache_set", new=AsyncMock()):
        resp = _client(pool, tier="pro").get("/api/stocks/GP/fundamentals")
    body = resp.json()
    assert body["max_years"] == 10
    assert body["is_truncated"] is False
    assert conn.fetch.await_args.args[-1] == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_api_stocks.py -k fundamentals -v`
Expected: FAIL — response has no `max_years`/`is_truncated`; cache key collides across tiers.

- [ ] **Step 3: Extend `FundamentalsResponse`**

In `api/schemas/stocks.py`, replace `FundamentalsResponse` (lines 82-84) with:

```python
class FundamentalsResponse(BaseModel):
    ticker: str
    items: list[FundamentalsRow]
    max_years: int
    is_truncated: bool
```

- [ ] **Step 4: Tier-gate `get_fundamentals`**

In `api/routers/stocks.py`, replace `get_fundamentals` (lines 315-340) with:

```python
@router.get("/{ticker}/fundamentals", response_model=FundamentalsResponse)
async def get_fundamentals(
    ticker: str, pool=Depends(get_db), user=Depends(get_current_user)
):
    ticker = ticker.upper()
    max_years = 10 if user["tier"] == "pro" else 3
    cache_key = f"cache:api:stocks:fundamentals:{ticker}:{max_years}"
    cached = await _cache_get(cache_key)
    if cached:
        return cached

    async with pool.acquire() as conn:
        exists = await conn.fetchrow("SELECT 1 FROM companies WHERE ticker = $1", ticker)
        if not exists:
            raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found")
        rows = await conn.fetch(
            """
            SELECT fiscal_year, eps, nav, pe, cash_div_pct, stock_div_pct,
                   sponsor_pct, public_pct, fetched_at
            FROM fundamentals WHERE ticker = $1
            ORDER BY fiscal_year DESC NULLS LAST, fetched_at DESC
            LIMIT $2
            """,
            ticker,
            max_years,
        )

    result = {
        "ticker": ticker,
        "items": [dict(r) for r in rows],
        "max_years": max_years,
        "is_truncated": max_years < 10,
    }
    await _cache_set(cache_key, result, ttl=86400)
    return result
```

> `is_truncated` means "more history exists behind the pro tier" — true whenever the cap is below the pro maximum. The cache key now includes `max_years` so free and pro responses don't clobber each other.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_api_stocks.py -v`
Expected: PASS (all four tests).

- [ ] **Step 6: Commit**

```bash
git add api/schemas/stocks.py api/routers/stocks.py tests/unit/test_api_stocks.py
git commit -m "feat(api): tier-gate fundamentals depth (free 3yr / pro 10yr)"
```

---

## Task 3: Add test coverage for the `/sectors/{sector}` detail endpoint

The endpoint exists (`api/routers/sectors.py:48`) but only the list endpoint is tested. Lock in its shape before the frontend depends on it.

**Files:**
- Test: `tests/unit/test_api_sectors.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_api_sectors.py`:

```python
from __future__ import annotations
import datetime as dt
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_current_user, get_db


def _client(conn):
    from api.routers.sectors import router
    app = FastAPI()
    app.include_router(router, prefix="/api")
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=conn)
    acquire.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock(); pool.acquire = MagicMock(return_value=acquire)
    app.dependency_overrides[get_db] = lambda: pool
    app.dependency_overrides[get_current_user] = lambda: {"id": 1, "tier": "free"}
    return TestClient(app)


def test_get_sector_detail_returns_history_and_companies():
    conn = MagicMock()
    pe_rows = [{
        "sector": "Telecom", "pe": Decimal("15.5"), "change_pct": Decimal("1.2"),
        "market_cap_bdt": Decimal("1000"), "fetched_at": dt.datetime(2026, 1, 1),
    }]
    conn.fetch = AsyncMock(side_effect=[pe_rows, [{"ticker": "GP"}, {"ticker": "ROBI"}]])
    with patch("api.routers.sectors._cache_get", new=AsyncMock(return_value=None)), \
         patch("api.routers.sectors._cache_set", new=AsyncMock()):
        resp = _client(conn).get("/api/sectors/Telecom")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sector"] == "Telecom"
    assert body["latest_pe"]["pe"] == "15.5"
    assert body["companies"] == ["GP", "ROBI"]


def test_get_sector_detail_404_when_unknown():
    conn = MagicMock()
    conn.fetch = AsyncMock(side_effect=[[], []])
    with patch("api.routers.sectors._cache_get", new=AsyncMock(return_value=None)), \
         patch("api.routers.sectors._cache_set", new=AsyncMock()):
        resp = _client(conn).get("/api/sectors/Nope")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/unit/test_api_sectors.py -v`
Expected: PASS — these tests document existing behaviour (no source change). If either fails, the endpoint regressed; fix `api/routers/sectors.py` before continuing.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_api_sectors.py
git commit -m "test(api): cover sector detail endpoint (history + companies + 404)"
```

---

## Task 4: Frontend types, JWT tier decode, and sectors API helper

Foundation for the screener and sectors pages: new types, a tier reader, and SSR/client fetch helpers.

**Files:**
- Modify: `frontend/lib/types.ts:60-67` (`StockListItem`) + append sector types
- Create: `frontend/lib/jwt.ts`
- Modify: `frontend/lib/api.ts` (add `sectors` client helper)
- Modify: `frontend/lib/server-api.ts` (add `serverApi.sectors`)
- Test: `frontend/__tests__/lib/jwt.test.ts` (create)

- [ ] **Step 1: Write the failing test for the tier decoder**

Create `frontend/__tests__/lib/jwt.test.ts`:

```ts
import { decodeTier } from '@/lib/jwt'

function makeToken(payload: object): string {
  const b64 = (o: object) =>
    Buffer.from(JSON.stringify(o)).toString('base64url')
  return `${b64({ alg: 'HS256' })}.${b64(payload)}.sig`
}

describe('decodeTier', () => {
  it('reads the tier claim from a token', () => {
    expect(decodeTier(makeToken({ sub: '1', tier: 'pro' }))).toBe('pro')
  })
  it('defaults to free when claim missing', () => {
    expect(decodeTier(makeToken({ sub: '1' }))).toBe('free')
  })
  it('defaults to free for null or malformed tokens', () => {
    expect(decodeTier(null)).toBe('free')
    expect(decodeTier('garbage')).toBe('free')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx jest jwt.test.ts`
Expected: FAIL — `@/lib/jwt` does not exist.

- [ ] **Step 3: Implement `frontend/lib/jwt.ts`**

```ts
// frontend/lib/jwt.ts
// UI-only tier read. The server is the source of truth for enforcement;
// this decode is NOT signature-verified and must never gate real access.
export type Tier = 'free' | 'pro'

export function decodeTier(token: string | null | undefined): Tier {
  if (!token) return 'free'
  try {
    const payload = token.split('.')[1]
    if (!payload) return 'free'
    const json =
      typeof atob === 'function'
        ? atob(payload.replace(/-/g, '+').replace(/_/g, '/'))
        : Buffer.from(payload, 'base64').toString('utf-8')
    const tier = JSON.parse(json).tier
    return tier === 'pro' ? 'pro' : 'free'
  } catch {
    return 'free'
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx jest jwt.test.ts`
Expected: PASS.

- [ ] **Step 5: Update `StockListItem` and add sector types**

In `frontend/lib/types.ts`, replace `StockListItem` (lines 60-67) with:

```ts
export interface StockListItem {
  ticker: string
  name: string
  sector: string
  category: string | null
  market_cap_bdt: number | null
  is_active: boolean
  pe: number | null
  health_score: number | null
  last_close: number | null
  change_pct: number | null
  rating: 'STRONG_BUY' | 'BUY' | 'HOLD' | 'SELL' | 'STRONG_SELL' | 'N/A'
}
```

Append to the end of `frontend/lib/types.ts`:

```ts
// ─── Sectors ───────────────────────────────────────────────────────────────
export interface SectorRow {
  sector: string
  pe: number | null
  change_pct: number | null
  market_cap_bdt: number | null
  fetched_at: string | null
}

export interface SectorDetail {
  sector: string
  latest_pe: SectorRow | null
  pe_history: SectorRow[]
  companies: string[]
}
```

> Backend returns `Decimal` as JSON strings, but the existing frontend already coerces with `Number(...)` at use sites (see `HeatmapGrid`); typing these as `number | null` matches the existing convention. Coerce with `Number()` where arithmetic is needed.

- [ ] **Step 6: Add the sectors helpers**

In `frontend/lib/server-api.ts`, add to the imports and the `serverApi` object:

```ts
// add to the type import block:
//   SectorRow, SectorDetail,

  sectors: {
    list: () => serverGet<SectorRow[]>('/api/sectors'),
    detail: (sector: string) =>
      serverGet<SectorDetail>(`/api/sectors/${encodeURIComponent(sector)}`),
  },
```

In `frontend/lib/api.ts`, add a client helper mirroring the existing `get` usage (read the file first to match its exact export style):

```ts
import type { SectorRow } from './types'
export const fetchSectors = () => get<SectorRow[]>('/api/sectors')
```

- [ ] **Step 7: Run lint + full frontend test suite**

Run: `cd frontend && npm run lint && npx jest`
Expected: PASS, no type errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/lib/types.ts frontend/lib/jwt.ts frontend/lib/api.ts frontend/lib/server-api.ts frontend/__tests__/lib/jwt.test.ts
git commit -m "feat(frontend): add sector types, tier decoder, and sectors api helpers"
```

---

## Task 5: Add PE and rating filters to the stock screener

`frontend/app/(app)/stocks/page.tsx` already filters by sector + search. Add a max-PE input and a rating dropdown. Extract the filter predicate as a pure function so it's unit-testable.

**Files:**
- Create: `frontend/lib/screener.ts`
- Test: `frontend/__tests__/lib/screener.test.ts` (create)
- Modify: `frontend/app/(app)/stocks/page.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/__tests__/lib/screener.test.ts`:

```ts
import { matchesFilters } from '@/lib/screener'
import type { StockListItem } from '@/lib/types'

const base: StockListItem = {
  ticker: 'GP', name: 'Grameenphone', sector: 'Telecom', category: 'A',
  market_cap_bdt: 1000, is_active: true, pe: 12, health_score: 82,
  last_close: 300, change_pct: 1, rating: 'STRONG_BUY',
}

describe('matchesFilters', () => {
  it('passes when all filters are at defaults', () => {
    expect(matchesFilters(base, { sector: 'All', search: '', maxPe: null, rating: 'All' })).toBe(true)
  })
  it('rejects when pe exceeds maxPe', () => {
    expect(matchesFilters(base, { sector: 'All', search: '', maxPe: 10, rating: 'All' })).toBe(false)
  })
  it('keeps rows with null pe only when maxPe is unset', () => {
    const noPe = { ...base, pe: null }
    expect(matchesFilters(noPe, { sector: 'All', search: '', maxPe: 10, rating: 'All' })).toBe(false)
    expect(matchesFilters(noPe, { sector: 'All', search: '', maxPe: null, rating: 'All' })).toBe(true)
  })
  it('filters by exact rating', () => {
    expect(matchesFilters(base, { sector: 'All', search: '', maxPe: null, rating: 'BUY' })).toBe(false)
    expect(matchesFilters(base, { sector: 'All', search: '', maxPe: null, rating: 'STRONG_BUY' })).toBe(true)
  })
  it('matches search against ticker and name, case-insensitively', () => {
    expect(matchesFilters(base, { sector: 'All', search: 'grameen', maxPe: null, rating: 'All' })).toBe(true)
    expect(matchesFilters(base, { sector: 'All', search: 'xyz', maxPe: null, rating: 'All' })).toBe(false)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx jest screener.test.ts`
Expected: FAIL — `@/lib/screener` does not exist.

- [ ] **Step 3: Implement `frontend/lib/screener.ts`**

```ts
// frontend/lib/screener.ts
import type { StockListItem } from './types'

export interface ScreenerFilters {
  sector: string          // 'All' or a sector name
  search: string
  maxPe: number | null    // null = no PE ceiling
  rating: string          // 'All' or a rating value
}

export function matchesFilters(s: StockListItem, f: ScreenerFilters): boolean {
  if (f.sector !== 'All' && s.sector !== f.sector) return false
  if (f.rating !== 'All' && s.rating !== f.rating) return false
  if (f.maxPe !== null) {
    if (s.pe === null) return false
    if (Number(s.pe) > f.maxPe) return false
  }
  if (f.search.trim()) {
    const q = f.search.toLowerCase()
    if (!s.ticker.toLowerCase().includes(q) && !s.name.toLowerCase().includes(q)) {
      return false
    }
  }
  return true
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx jest screener.test.ts`
Expected: PASS.

- [ ] **Step 5: Wire the filters into the screener page**

In `frontend/app/(app)/stocks/page.tsx`:

1. Add imports:
```ts
import { matchesFilters, type ScreenerFilters } from '@/lib/screener'
```
2. Add state next to the existing `sector`/`search` state (after line 30):
```ts
  const [maxPe, setMaxPe] = useState<number | null>(null)
  const [rating, setRating] = useState('All')
  const RATINGS = ['All', 'STRONG_BUY', 'BUY', 'HOLD', 'SELL', 'STRONG_SELL']
```
3. Replace the body of the `filtered` `useMemo` (lines 55-71) so filtering delegates to `matchesFilters`, keeping the existing sort:
```ts
  const filtered = useMemo(() => {
    const f: ScreenerFilters = { sector, search, maxPe, rating }
    const s = stocks.filter(x => matchesFilters(x, f))
    return [...s].sort((a, b) => {
      let av: string | number = a[sortKey] ?? ''
      let bv: string | number = b[sortKey] ?? ''
      if (typeof av === 'string') av = av.toLowerCase()
      if (typeof bv === 'string') bv = bv.toLowerCase()
      if (av < bv) return sortAsc ? -1 : 1
      if (av > bv) return sortAsc ? 1 : -1
      return 0
    })
  }, [stocks, sector, search, maxPe, rating, sortKey, sortAsc])
```
4. In the filter bar (after the sector `<select>`, ~line 112), add a PE input and rating select, matching the existing input styling:
```tsx
        <input
          type="number"
          placeholder="Max P/E"
          value={maxPe ?? ''}
          onChange={e => setMaxPe(e.target.value === '' ? null : Number(e.target.value))}
          className="w-28 bg-[#1a1a24] border border-[#2a2a3a] rounded-lg px-3 py-2 text-sm font-mono text-[#e8e8f0] placeholder:text-[#6b6b80] focus:outline-none focus:border-[#4d9eff]/50 transition-colors"
        />
        <select
          value={rating}
          onChange={e => setRating(e.target.value)}
          className="bg-[#1a1a24] border border-[#2a2a3a] rounded-lg px-3 py-2 text-sm font-mono text-[#e8e8f0] focus:outline-none focus:border-[#4d9eff]/50 transition-colors"
        >
          {RATINGS.map(r => <option key={r} value={r}>{r === 'All' ? 'All ratings' : r.replace('_', ' ')}</option>)}
        </select>
```
5. Update the "Clear" button condition (line 113) and handler to also reset the new filters:
```tsx
        {(search || sector !== 'All' || maxPe !== null || rating !== 'All') && (
          <button
            onClick={() => { setSearch(''); setSector('All'); setMaxPe(null); setRating('All') }}
```
6. Add a PE column and a rating column to the table so the filters are legible (optional but recommended — mirror the existing `<th>`/`<td>` markup; render rating via the existing `RatingBadge` by importing it and passing `score={stock.health_score}`).

- [ ] **Step 6: Verify build + lint**

Run: `cd frontend && npm run lint && npm run build`
Expected: build succeeds, no type errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/lib/screener.ts frontend/__tests__/lib/screener.test.ts "frontend/app/(app)/stocks/page.tsx"
git commit -m "feat(frontend): add PE and rating filters to stock screener"
```

---

## Task 6: Wire PaywallOverlay onto the pro-only fundamentals depth

`FundamentalsResponse` now carries `is_truncated`. On the stock detail page, when `is_truncated` is true (free tier), wrap the fundamentals chart in `PaywallOverlay` so free users see blurred 3-year data with an upgrade CTA.

**Files:**
- Modify: `frontend/lib/types.ts` (`FundamentalsResponse`)
- Modify: `frontend/app/(app)/stocks/[ticker]/page.tsx:184-192`

- [ ] **Step 1: Update the `FundamentalsResponse` type**

In `frontend/lib/types.ts`, find `FundamentalsResponse` (around line 164) and add the two fields:

```ts
export interface FundamentalsResponse {
  ticker: string
  items: FundamentalsRow[]
  max_years: number
  is_truncated: boolean
}
```

- [ ] **Step 2: Gate the fundamentals section behind the paywall**

In `frontend/app/(app)/stocks/[ticker]/page.tsx`:

1. Add import:
```ts
import PaywallOverlay from '@/components/ui/PaywallOverlay'
```
2. Replace the fundamentals block (lines 184-192) with a version that conditionally wraps in the overlay:
```tsx
      {/* ── Fundamentals ─────────────────────────────────────────────────────── */}
      {fundsData && fundsData.items.length > 0 && (
        <div className="bg-[#111118] border border-[#2a2a3a] rounded-xl p-5">
          <p className="text-[10px] font-mono uppercase tracking-[0.18em] text-[#6b6b80] mb-4">
            Historical Fundamentals
          </p>
          {fundsData.is_truncated ? (
            <PaywallOverlay feature="Unlock 10 years of fundamentals with Pro">
              <FundamentalsCharts items={fundsData.items} />
            </PaywallOverlay>
          ) : (
            <FundamentalsCharts items={fundsData.items} />
          )}
        </div>
      )}
```

> Free tier still gets the 3-year chart rendered underneath the blur (matching `PaywallOverlay`'s existing blur-the-children design). The real depth limit is enforced server-side in Task 2 — this is presentation only.

- [ ] **Step 3: Verify build**

Run: `cd frontend && npm run lint && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/lib/types.ts "frontend/app/(app)/stocks/[ticker]/page.tsx"
git commit -m "feat(frontend): paywall pro-depth fundamentals for free tier"
```

---

## Task 7: Build the `SectorHeatmap` component (sector-level treemap)

A treemap with one tile per sector: tile area = `market_cap_bdt`, fill color = `change_pct` (reusing the proven color scale + squarify from `HeatmapGrid.tsx`). Extract `squarify` to a shared module so both components use one tested implementation.

**Files:**
- Create: `frontend/lib/treemap.ts` (extract `squarify` + helpers)
- Modify: `frontend/components/market/HeatmapGrid.tsx` (import from `treemap.ts`)
- Create: `frontend/components/sectors/SectorHeatmap.tsx`
- Test: `frontend/__tests__/lib/treemap.test.ts` (create)
- Test: `frontend/__tests__/components/sectors/SectorHeatmap.test.tsx` (create)

- [ ] **Step 1: Write the failing test for the extracted treemap**

Create `frontend/__tests__/lib/treemap.test.ts`:

```ts
import { squarify } from '@/lib/treemap'

describe('squarify', () => {
  it('tiles fill the rect area (sum of areas ≈ rect area)', () => {
    const rect = { x: 0, y: 0, w: 100, h: 100 }
    const rects = squarify([4, 3, 2, 1], rect)
    expect(rects).toHaveLength(4)
    const area = rects.reduce((s, r) => s + r.w * r.h, 0)
    expect(area).toBeCloseTo(100 * 100, 0)
  })
  it('returns the full rect for every item when total is 0', () => {
    const rect = { x: 0, y: 0, w: 50, h: 50 }
    expect(squarify([0, 0], rect)).toEqual([rect, rect])
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx jest treemap.test.ts`
Expected: FAIL — `@/lib/treemap` does not exist.

- [ ] **Step 3: Extract the treemap module**

Create `frontend/lib/treemap.ts` by moving the `Rect` interface, `worstAspect`, `squarify`, and `layout` functions verbatim out of `HeatmapGrid.tsx` (lines 7-62) and exporting them:

```ts
// frontend/lib/treemap.ts
export interface Rect { x: number; y: number; w: number; h: number }

function worstAspect(row: number[], rowLen: number): number {
  const s = row.reduce((a, b) => a + b, 0)
  const rowW = s / rowLen
  let worst = 0
  for (const a of row) {
    const h = a / rowW
    worst = Math.max(worst, Math.max(rowW / h, h / rowW))
  }
  return worst
}

export function squarify(values: number[], rect: Rect): Rect[] {
  const total = values.reduce((a, b) => a + b, 0)
  if (total === 0 || rect.w <= 0 || rect.h <= 0) return values.map(() => rect)
  const area = rect.w * rect.h
  const scaled = values.map(v => (v / total) * area)
  const out: Rect[] = new Array(values.length)
  layout(scaled, rect, 0, out)
  return out
}

function layout(areas: number[], rect: Rect, offset: number, out: Rect[]) {
  if (areas.length === 0) return
  if (areas.length === 1) { out[offset] = rect; return }
  const { x, y, w, h } = rect
  const horizontal = w >= h
  const side = horizontal ? h : w
  let row: number[] = []
  let i = 0
  for (; i < areas.length; i++) {
    const candidate = [...row, areas[i]]
    if (row.length > 0 && worstAspect(candidate, side) > worstAspect(row, side)) break
    row = candidate
  }
  const rowSum = row.reduce((a, b) => a + b, 0)
  const rowDim = rowSum / side
  let pos = horizontal ? y : x
  for (let j = 0; j < row.length; j++) {
    const itemDim = row[j] / rowDim
    out[offset + j] = horizontal
      ? { x, y: pos, w: rowDim, h: itemDim }
      : { x: pos, y, w: itemDim, h: rowDim }
    pos += itemDim
  }
  const nextRect = horizontal
    ? { x: x + rowDim, y, w: w - rowDim, h }
    : { x, y: y + rowDim, w, h: h - rowDim }
  layout(areas.slice(i), nextRect, offset + i, out)
}
```

Then in `frontend/components/market/HeatmapGrid.tsx`, delete lines 7-62 and add at the top:

```ts
import { squarify, type Rect } from '@/lib/treemap'
```

- [ ] **Step 4: Run treemap + existing heatmap tests to verify the extraction is safe**

Run: `cd frontend && npx jest treemap.test.ts HeatmapGrid.test.tsx`
Expected: PASS — extraction preserved behaviour; the existing `HeatmapGrid` test still passes.

- [ ] **Step 5: Write the failing test for `SectorHeatmap`**

Create `frontend/__tests__/components/sectors/SectorHeatmap.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import SectorHeatmap from '@/components/sectors/SectorHeatmap'
import type { SectorRow } from '@/lib/types'

const rows: SectorRow[] = [
  { sector: 'Telecom', pe: 15, change_pct: 2.1, market_cap_bdt: 5000, fetched_at: null },
  { sector: 'Bank', pe: 9, change_pct: -1.4, market_cap_bdt: 8000, fetched_at: null },
]

describe('SectorHeatmap', () => {
  it('renders a tile labelled for each sector', () => {
    render(<SectorHeatmap sectors={rows} />)
    expect(screen.getByText('Telecom')).toBeInTheDocument()
    expect(screen.getByText('Bank')).toBeInTheDocument()
  })
  it('shows the change percent on tiles', () => {
    render(<SectorHeatmap sectors={rows} />)
    expect(screen.getByText('+2.1%')).toBeInTheDocument()
    expect(screen.getByText('-1.4%')).toBeInTheDocument()
  })
})
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd frontend && npx jest SectorHeatmap.test.tsx`
Expected: FAIL — component does not exist.

- [ ] **Step 7: Implement `SectorHeatmap`**

Create `frontend/components/sectors/SectorHeatmap.tsx`. Reuse the `cellStyle` color thresholds from `HeatmapGrid.tsx` (copy them; they're trivial) and the shared `squarify`:

```tsx
'use client'
import { useRef, useState, useEffect } from 'react'
import { squarify, type Rect } from '@/lib/treemap'
import type { SectorRow } from '@/lib/types'

function cellStyle(pct: number | null): { bg: string; text: string } {
  if (pct === null) return { bg: '#1a1a24', text: '#6b6b80' }
  if (pct > 6)  return { bg: '#15803d', text: '#fff' }
  if (pct > 3)  return { bg: '#166534', text: '#fff' }
  if (pct > 0)  return { bg: '#14532d', text: '#d1fae5' }
  if (pct === 0) return { bg: '#1a1a24', text: '#6b6b80' }
  if (pct > -3) return { bg: '#7f1d1d', text: '#fee2e2' }
  if (pct > -6) return { bg: '#991b1b', text: '#fff' }
  return { bg: '#b91c1c', text: '#fff' }
}

export default function SectorHeatmap({ sectors }: { sectors: SectorRow[] }) {
  const ref = useRef<HTMLDivElement>(null)
  const [dims, setDims] = useState({ w: 800, h: 480 })

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const obs = new ResizeObserver(([e]) => {
      const { width } = e.contentRect
      setDims({ w: width, h: Math.max(300, Math.round(width * 0.55)) })
    })
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  const sorted = [...sectors].sort(
    (a, b) => Number(b.market_cap_bdt ?? 1) - Number(a.market_cap_bdt ?? 1)
  )
  const values = sorted.map(s => Math.max(1, Number(s.market_cap_bdt ?? 1)))
  const rects: Rect[] = squarify(values, { x: 0, y: 0, w: dims.w, h: dims.h })

  return (
    <div className="bg-[#111118] border border-[#2a2a3a] rounded-xl p-4">
      <h3 className="text-xs text-[#6b6b80] uppercase tracking-widest mb-3">Sector Performance</h3>
      <div ref={ref} className="relative overflow-hidden rounded" style={{ height: dims.h }}>
        {sorted.map((s, i) => {
          const r = rects[i]
          if (!r || r.w < 2 || r.h < 2) return null
          const pct = s.change_pct === null ? null : Number(s.change_pct)
          const { bg, text } = cellStyle(pct)
          const showLabel = r.w > 54 && r.h > 28
          return (
            <div
              key={s.sector}
              className="absolute flex flex-col items-center justify-center overflow-hidden text-center px-1"
              style={{ left: r.x + 1, top: r.y + 1, width: Math.max(0, r.w - 2), height: Math.max(0, r.h - 2), background: bg, borderRadius: 3 }}
            >
              {showLabel && (
                <>
                  <span className="font-semibold leading-tight truncate w-full" style={{ color: text, fontSize: Math.min(13, Math.max(8, r.w / 9)) }}>
                    {s.sector}
                  </span>
                  {pct !== null && (
                    <span className="leading-none mt-0.5" style={{ color: text, fontSize: Math.min(12, Math.max(8, r.w / 11)), opacity: 0.9 }}>
                      {pct > 0 ? '+' : ''}{pct.toFixed(1)}%
                    </span>
                  )}
                </>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
```

> Test note: the `ResizeObserver` never fires in jsdom, so `dims` keeps its `{w:800,h:480}` default — large enough that `showLabel` is true and the assertions in Step 5 pass. Ensure `ResizeObserver` is polyfilled in the jest setup; `HeatmapGrid.test.tsx` already runs, so the polyfill exists — mirror its setup import if the test errors on `ResizeObserver is not defined`.

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd frontend && npx jest treemap.test.ts HeatmapGrid.test.tsx SectorHeatmap.test.tsx`
Expected: PASS (all suites).

- [ ] **Step 9: Commit**

```bash
git add frontend/lib/treemap.ts frontend/components/market/HeatmapGrid.tsx frontend/components/sectors/SectorHeatmap.tsx "frontend/__tests__/lib/treemap.test.ts" "frontend/__tests__/components/sectors/SectorHeatmap.test.tsx"
git commit -m "feat(frontend): extract treemap module + add SectorHeatmap component"
```

---

## Task 8: Build the `/sectors` page (SSR: PE table + heatmap)

Server Component fetching `serverApi.sectors.list()`, rendering the `SectorHeatmap` above a sortable-by-default PE table. Sidebar already links here.

**Files:**
- Create: `frontend/app/(app)/sectors/page.tsx`

- [ ] **Step 1: Read the Next.js 16 Server Component + data-fetching guide**

Read the relevant file under `frontend/node_modules/next/dist/docs/` for async Server Components and `fetch`. Confirm the SSR page signature matches `frontend/app/(app)/stocks/[ticker]/page.tsx` (an `async function` default export).

- [ ] **Step 2: Implement the page**

Create `frontend/app/(app)/sectors/page.tsx`:

```tsx
import { serverApi } from '@/lib/server-api'
import SectorHeatmap from '@/components/sectors/SectorHeatmap'
import type { SectorRow } from '@/lib/types'

function fmtCap(bdt: number | null): string {
  if (!bdt) return '—'
  const cr = Number(bdt) / 10_000_000
  if (cr >= 1000) return `৳${(cr / 1000).toFixed(1)}K Cr`
  return `৳${cr.toFixed(0)} Cr`
}

function fmtPe(pe: number | null): string {
  return pe == null ? '—' : Number(pe).toFixed(1)
}

export default async function SectorsPage() {
  let sectors: SectorRow[] = []
  let error: string | null = null
  try {
    sectors = await serverApi.sectors.list()
  } catch {
    error = 'Failed to load sectors'
  }

  // Largest market cap first for the table.
  const rows = [...sectors].sort(
    (a, b) => Number(b.market_cap_bdt ?? 0) - Number(a.market_cap_bdt ?? 0)
  )

  return (
    <div className="space-y-5 max-w-[1400px]">
      <div>
        <h1 className="text-2xl font-bold text-white tracking-tight">Sectors</h1>
        <p className="text-[#6b6b80] text-sm font-mono mt-0.5">
          {error ? '—' : `${rows.length} sectors`}
        </p>
      </div>

      {error ? (
        <div className="bg-[#111118] border border-[#2a2a3a] rounded-xl py-16 text-center text-[#ff4d6a] font-mono text-sm">
          {error}
        </div>
      ) : (
        <>
          <SectorHeatmap sectors={rows} />

          <div className="bg-[#111118] border border-[#2a2a3a] rounded-xl overflow-hidden">
            <table className="w-full">
              <thead>
                <tr className="border-b border-[#2a2a3a]">
                  <th className="py-3 pl-4 pr-2 text-left text-[10px] font-mono uppercase tracking-wider text-[#6b6b80]">Sector</th>
                  <th className="py-3 px-2 text-right text-[10px] font-mono uppercase tracking-wider text-[#6b6b80]">P/E</th>
                  <th className="py-3 px-2 text-right text-[10px] font-mono uppercase tracking-wider text-[#6b6b80]">Change</th>
                  <th className="py-3 pl-2 pr-4 text-right text-[10px] font-mono uppercase tracking-wider text-[#6b6b80]">Market Cap</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(s => {
                  const chg = s.change_pct == null ? null : Number(s.change_pct)
                  return (
                    <tr key={s.sector} className="border-b border-[#1a1a24] hover:bg-[#0f0f18] transition-colors">
                      <td className="py-3 pl-4 pr-2 text-sm text-[#e8e8f0]">{s.sector}</td>
                      <td className="py-3 px-2 text-right text-sm font-mono tabular-nums text-[#e8e8f0]">{fmtPe(s.pe)}</td>
                      <td className={`py-3 px-2 text-right text-sm font-mono tabular-nums ${chg == null ? 'text-[#6b6b80]' : chg >= 0 ? 'text-[#00d4a4]' : 'text-[#ff4d6a]'}`}>
                        {chg == null ? '—' : `${chg > 0 ? '+' : ''}${chg.toFixed(2)}%`}
                      </td>
                      <td className="py-3 pl-2 pr-4 text-right text-sm font-mono tabular-nums text-[#e8e8f0]">{fmtCap(s.market_cap_bdt)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <p className="text-[10px] font-mono text-[#6b6b80] text-center py-2">
            Sector P/E sourced from DSE. For informational purposes only. Not investment advice.
          </p>
        </>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Verify build + lint**

Run: `cd frontend && npm run lint && npm run build`
Expected: build succeeds; `/sectors` compiles as a static/SSR route.

- [ ] **Step 4: Commit**

```bash
git add "frontend/app/(app)/sectors/page.tsx"
git commit -m "feat(frontend): add /sectors page with PE table and sector heatmap"
```

---

## Task 9: Final verification

- [ ] **Step 1: Full backend test + quality gate**

Run: `make test && make check`
Expected: all unit tests pass; lint + mypy clean.

- [ ] **Step 2: Full frontend test + build**

Run: `cd frontend && npx jest && npm run lint && npm run build`
Expected: all suites pass; production build succeeds.

- [ ] **Step 3: Manual smoke (requires running stack)**

Use the `verify` skill (or `make up`, then browse). Confirm:
- `/stocks` — PE input and rating dropdown filter the list; columns show PE + rating.
- `/stocks/[ticker]` as a **free** user — fundamentals chart is blurred behind the Pro paywall; as **pro** — full 10yr chart, no blur.
- `/sectors` — heatmap renders one tile per sector (size ∝ market cap, color ∝ change%), PE table below it; no 404.

- [ ] **Step 4: Update the spec checklist**

In `docs/superpowers/specs/2026-05-26-consumer-frontend-design.md`, tick the Phase C boxes (lines 299-305). Leave predictions-related items for Phase E.

```bash
git add docs/superpowers/specs/2026-05-26-consumer-frontend-design.md
git commit -m "docs: mark Phase C checklist complete"
```

---

## Self-Review (completed during planning)

**Spec coverage:** Every Phase C checklist line maps to a task (see Audit table). `/stocks/[ticker]` PriceChart/HealthGauge/RatingBadge already shipped — no task needed. Predictions pro-gating deliberately deferred to Phase E and called out.

**Type consistency:** `CompanyRow` (backend) gains `pe, health_score, last_close, change_pct, rating`; `StockListItem` (frontend) mirrors them exactly. `rating` values use underscore form (`STRONG_BUY`) on the wire/screener; `RatingBadge` renders its own spaced labels from `health_score` independently — both derive from the same thresholds, kept in sync by the shared 80/60/40/20 cutoffs. `FundamentalsResponse` gains `max_years, is_truncated` on both sides. `SectorRow`/`SectorDetail` match `api/schemas/sectors.py`. `squarify`/`Rect` exported from one module, imported by both `HeatmapGrid` and `SectorHeatmap`.

**Placeholder scan:** No TBD/TODO/"handle edge cases" — every code step has concrete code; every test step has runnable assertions.
