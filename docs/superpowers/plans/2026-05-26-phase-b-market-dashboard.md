# Phase B — Market Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build market dashboard: DSEX/DS30/DSES index cards, gainers/losers strip, sector heatmap, live TopBar SSE strip, and landing page.

**Architecture:** Backend adds `GET /api/market/indices` (httpx → AmarStock, Redis cache 60s) and `GET /api/market/stream` (SSE, 30s poll, no auth). Frontend is SSR-first: the dashboard page is a Server Component that fetches via `server-api.ts` using the session cookie. The TopBar gets a client-only `MarketStreamBar` component that subscribes to the SSE stream. The root `/` page becomes the marketing landing page instead of an unconditional redirect.

**Tech Stack:** FastAPI (`httpx`, `StreamingResponse`), Next.js 16 Server Components, `next/headers` cookies, EventSource (native browser API), Tailwind CSS v4, shadcn/ui

---

## Context for agentic workers

**Repo root:** `D:\projects\bdmarcket-analysis`

**Existing API files (already built — do not recreate):**
- `api/main.py` — FastAPI app, registers routers with `/api` prefix
- `api/routers/market.py` — already has `/market/summary`, `/market/movers`, `/market/heatmap`
- `api/schemas/market.py` — already has `MarketSummary`, `TopMover`, `HeatmapItem`
- `api/middleware/rate_limit.py` — skips paths in `_SKIP_PREFIXES`; passes unauthenticated requests through unblocked
- `api/deps.py` — `get_current_user`, `get_db`

**Existing frontend files (already built — do not recreate):**
- `frontend/lib/api.ts` — client-side fetch wrapper (reads cookie via `getAccessToken()`)
- `frontend/lib/auth.ts` — `getAccessToken()`, `setTokens()`, `clearTokens()`
- `frontend/app/(app)/layout.tsx` — renders `<Sidebar>` + `<TopBar>` + `<main>`
- `frontend/components/layout/TopBar.tsx` — static header, needs live strip added
- `frontend/app/(app)/dashboard/page.tsx` — placeholder, replace entirely

**AmarStock `/Info/DSE` response shape (from real fixture):**
```json
{
  "IndexValue": 5330.89,   "Change": 66.77,   "ChangePct": 1.27,
  "DsIndex":   1078.43,   "DsChange": 8.95,  "DsChangePct": 0.84,
  "D30Index":  2023.89,   "D30Change": 28.53, "D30ChangePct": 1.43,
  "TotalTrade": 142982,   "TotalVolume": 187312467, "TotalValue": 5816.28,
  "Advance": 271, "Decline": 67, "Unchange": 68,
  "MarketStatus": "Open"
}
```

**Design tokens (Tailwind v4 `@theme` block in `frontend/app/globals.css`):**
- `bg-base` (#0a0a0f), `bg-surface` (#111118), `bg-elevated` (#1a1a24)
- `border-border-custom` (#2a2a3a)
- `text-muted` (#6b6b80)
- `text-accent-green` (#00d4a4), `text-accent-red` (#ff4d6a)
- `text-accent-blue` (#4d9eff), `text-accent-gold` (#f5c842)

**Test command (from repo root):** `cd frontend && npm test`
**Single test:** `cd frontend && npm test -- --testPathPattern=<filename>`
**Python test:** `pytest tests/unit/<file>.py -v`

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `api/schemas/market.py` | Modify | Add `MarketIndices` schema |
| `api/routers/market.py` | Modify | Add `/indices` + `/stream` endpoints |
| `tests/unit/test_api_market_indices.py` | Create | Test `_fetch_indices_from_amarstock` + indices endpoint |
| `frontend/lib/server-api.ts` | Create | Server-side fetch helper using `next/headers` cookies |
| `frontend/components/market/IndexCard.tsx` | Create | DSEX/DS30/DSES index card |
| `frontend/components/market/MoverStrip.tsx` | Create | Horizontal gainers/losers strip |
| `frontend/components/market/HeatmapGrid.tsx` | Create | Sector heatmap grid |
| `frontend/__tests__/components/market/IndexCard.test.tsx` | Create | IndexCard tests |
| `frontend/__tests__/components/market/MoverStrip.test.tsx` | Create | MoverStrip tests |
| `frontend/__tests__/components/market/HeatmapGrid.test.tsx` | Create | HeatmapGrid tests |
| `frontend/app/(app)/dashboard/page.tsx` | Replace | Full SSR dashboard |
| `frontend/components/layout/MarketStreamBar.tsx` | Create | Client SSE bar for TopBar |
| `frontend/__tests__/components/layout/MarketStreamBar.test.tsx` | Create | MarketStreamBar tests |
| `frontend/components/layout/TopBar.tsx` | Replace | Renders MarketStreamBar |
| `frontend/app/page.tsx` | Replace | Landing page (not a redirect) |

---

### Task 1: Backend — `MarketIndices` schema + `/api/market/indices` endpoint

**Files:**
- Modify: `api/schemas/market.py`
- Modify: `api/routers/market.py`
- Create: `tests/unit/test_api_market_indices.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_api_market_indices.py`:

```python
# tests/unit/test_api_market_indices.py
import os
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.routers.market import router, _fetch_indices_from_amarstock


FAKE_AMARSTOCK = {
    "IndexValue": 5330.89, "Change": 66.77, "ChangePct": 1.27,
    "DsIndex": 1078.43, "DsChange": 8.95, "DsChangePct": 0.84,
    "D30Index": 2023.89, "D30Change": 28.53, "D30ChangePct": 1.43,
    "TotalTrade": 142982, "TotalVolume": 187312467, "TotalValue": 5816.28,
    "Advance": 271, "Decline": 67, "Unchange": 68, "MarketStatus": "Open",
}


def _mock_httpx(json_data: dict):
    mock_resp = MagicMock()
    mock_resp.json.return_value = json_data
    mock_resp.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)
    return mock_client


@pytest.mark.asyncio
async def test_fetch_indices_from_amarstock_maps_fields():
    with patch("api.routers.market.httpx.AsyncClient", return_value=_mock_httpx(FAKE_AMARSTOCK)):
        result = await _fetch_indices_from_amarstock()

    assert result["dsex_value"] == pytest.approx(5330.89)
    assert result["dsex_change_pct"] == pytest.approx(1.27)
    assert result["ds30_value"] == pytest.approx(2023.89)
    assert result["dses_value"] == pytest.approx(1078.43)
    assert result["market_status"] == "Open"
    assert result["advance"] == 271
    assert result["decline"] == 67
    assert result["unchanged"] == 68


@pytest.mark.asyncio
async def test_fetch_indices_from_amarstock_raises_on_http_error():
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = Exception("HTTP 503")
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)
    with patch("api.routers.market.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(Exception, match="HTTP 503"):
            await _fetch_indices_from_amarstock()
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest tests/unit/test_api_market_indices.py -v
```

Expected: `ImportError` or `AttributeError: module 'api.routers.market' has no attribute '_fetch_indices_from_amarstock'`

- [ ] **Step 3: Add `MarketIndices` schema**

In `api/schemas/market.py`, append:

```python
class MarketIndices(BaseModel):
    dsex_value: float
    dsex_change_pct: float
    ds30_value: float
    ds30_change_pct: float
    dses_value: float
    dses_change_pct: float
    market_status: str
    advance: int
    decline: int
    unchanged: int
```

- [ ] **Step 4: Add `_fetch_indices_from_amarstock` + `/indices` endpoint**

In `api/routers/market.py`, add these imports at the top (after existing imports):

```python
import httpx
import json as _json
from api.schemas.market import HeatmapItem, MarketSummary, MarketIndices, TopMover
```

Then add after the existing `_cache_set` helper:

```python
_AMARSTOCK_MARKET_URL = "https://www.amarstock.com/Info/DSE"
_AMARSTOCK_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DSEIntelBot/1.0)"}


async def _fetch_indices_from_amarstock() -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(_AMARSTOCK_MARKET_URL, headers=_AMARSTOCK_HEADERS)
        resp.raise_for_status()
        data = resp.json()
    return {
        "dsex_value": float(data["IndexValue"]),
        "dsex_change_pct": float(data["ChangePct"]),
        "ds30_value": float(data["D30Index"]),
        "ds30_change_pct": float(data["D30ChangePct"]),
        "dses_value": float(data["DsIndex"]),
        "dses_change_pct": float(data["DsChangePct"]),
        "market_status": data["MarketStatus"],
        "advance": int(data["Advance"]),
        "decline": int(data["Decline"]),
        "unchanged": int(data["Unchange"]),
    }


@router.get("/indices", response_model=MarketIndices)
async def market_indices(_user=Depends(get_current_user)):
    cache_key = "cache:api:market:indices"
    cached = await _cache_get(cache_key)
    if cached:
        return cached
    result = await _fetch_indices_from_amarstock()
    await _cache_set(cache_key, result, ttl=60)
    return result
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
pytest tests/unit/test_api_market_indices.py -v
```

Expected: 2 tests PASS

- [ ] **Step 6: Commit**

```bash
git add api/schemas/market.py api/routers/market.py tests/unit/test_api_market_indices.py
git commit -m "feat(api): market indices endpoint from AmarStock /Info/DSE with Redis cache"
```

---

### Task 2: Backend — `/api/market/stream` SSE endpoint

**Files:**
- Modify: `api/routers/market.py`
- Create: `tests/unit/test_api_market_stream.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_api_market_stream.py`:

```python
# tests/unit/test_api_market_stream.py
import os
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

import json
from unittest.mock import AsyncMock, patch
import pytest
from api.routers.market import _generate_market_events


FAKE_INDICES = {
    "dsex_value": 5330.89, "dsex_change_pct": 1.27,
    "ds30_value": 2023.89, "ds30_change_pct": 1.43,
    "dses_value": 1078.43, "dses_change_pct": 0.84,
    "market_status": "Open", "advance": 271, "decline": 67, "unchanged": 68,
}


@pytest.mark.asyncio
async def test_generate_market_events_yields_sse_format():
    async def _fake_get_cached():
        return FAKE_INDICES

    gen = _generate_market_events(get_indices_fn=_fake_get_cached, interval=0)
    event = await gen.__anext__()
    assert event.startswith("data: ")
    payload = json.loads(event[len("data: "):].strip())
    assert payload["dsex_value"] == pytest.approx(5330.89)
    assert payload["market_status"] == "Open"


@pytest.mark.asyncio
async def test_generate_market_events_on_error_yields_error_event():
    async def _fail():
        raise Exception("upstream down")

    gen = _generate_market_events(get_indices_fn=_fail, interval=0)
    event = await gen.__anext__()
    assert event.startswith("data: ")
    payload = json.loads(event[len("data: "):].strip())
    assert payload["error"] == "fetch_failed"
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest tests/unit/test_api_market_stream.py -v
```

Expected: `ImportError: cannot import name '_generate_market_events'`

- [ ] **Step 3: Add `_generate_market_events` + `/stream` endpoint**

In `api/routers/market.py`, add this import near the top:

```python
import asyncio
import json as _json
from fastapi.responses import StreamingResponse
```

Then append after the `/indices` endpoint:

```python
async def _generate_market_events(get_indices_fn=None, interval: int = 30):
    """Async generator that yields SSE-formatted market index events."""
    if get_indices_fn is None:
        async def get_indices_fn():
            cache_key = "cache:api:market:indices"
            cached = await _cache_get(cache_key)
            if cached:
                return cached
            result = await _fetch_indices_from_amarstock()
            await _cache_set(cache_key, result, ttl=60)
            return result

    while True:
        try:
            data = await get_indices_fn()
            yield f"data: {_json.dumps(data)}\n\n"
        except Exception:
            yield f"data: {_json.dumps({'error': 'fetch_failed'})}\n\n"
        if interval > 0:
            await asyncio.sleep(interval)
        else:
            return  # test mode: yield once then stop


@router.get("/stream")
async def market_stream():
    """SSE stream of market indices — no auth required (public data)."""
    return StreamingResponse(
        _generate_market_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_api_market_stream.py -v
```

Expected: 2 tests PASS

- [ ] **Step 5: Verify live endpoint works**

```bash
curl -N http://localhost:8000/api/market/stream
```

Expected: `data: {"dsex_value": 5330.89, ...}` (one line every 30s if AmarStock reachable, `{"error":"fetch_failed"}` if not)

- [ ] **Step 6: Commit**

```bash
git add api/routers/market.py tests/unit/test_api_market_stream.py
git commit -m "feat(api): SSE market stream endpoint at /api/market/stream"
```

---

### Task 3: Frontend — server-side API helper

**Files:**
- Create: `frontend/lib/server-api.ts`
- Create: `frontend/__tests__/lib/server-api.test.ts`

The dashboard page is a Server Component and fetches data at render time. It can't use `lib/api.ts` (client-only cookie reading). `server-api.ts` reads the auth cookie server-side via `next/headers`.

- [ ] **Step 1: Write the failing test**

Create `frontend/__tests__/lib/server-api.test.ts`:

```typescript
// @jest-environment node
// frontend/__tests__/lib/server-api.test.ts
import { cookies } from 'next/headers'
import { serverGet } from '@/lib/server-api'

jest.mock('next/headers', () => ({
  cookies: jest.fn(),
}))

const mockCookies = cookies as jest.Mock

global.fetch = jest.fn()

beforeEach(() => {
  jest.clearAllMocks()
  mockCookies.mockResolvedValue({
    get: jest.fn().mockReturnValue({ value: 'test-token-123' }),
  })
})

test('serverGet passes Authorization header when token in cookie', async () => {
  ;(global.fetch as jest.Mock).mockResolvedValue({
    ok: true,
    json: async () => ({ data: 'ok' }),
  })

  const result = await serverGet('/api/market/indices')

  expect(global.fetch).toHaveBeenCalledWith(
    expect.stringContaining('/api/market/indices'),
    expect.objectContaining({
      headers: expect.objectContaining({ Authorization: 'Bearer test-token-123' }),
    }),
  )
  expect(result).toEqual({ data: 'ok' })
})

test('serverGet throws on non-ok response', async () => {
  ;(global.fetch as jest.Mock).mockResolvedValue({
    ok: false,
    status: 503,
  })

  await expect(serverGet('/api/market/indices')).rejects.toThrow('HTTP 503')
})

test('serverGet omits Authorization if no cookie', async () => {
  mockCookies.mockResolvedValue({
    get: jest.fn().mockReturnValue(undefined),
  })
  ;(global.fetch as jest.Mock).mockResolvedValue({
    ok: true,
    json: async () => ({}),
  })

  await serverGet('/api/market/summary')

  const call = (global.fetch as jest.Mock).mock.calls[0]
  expect(call[1].headers.Authorization).toBeUndefined()
})
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd frontend && npm test -- --testPathPattern=server-api
```

Expected: `Cannot find module '@/lib/server-api'`

- [ ] **Step 3: Create `frontend/lib/server-api.ts`**

```typescript
// frontend/lib/server-api.ts
import { cookies } from 'next/headers'
import type { MarketIndices, MarketMovers, MarketSummary, HeatmapItem } from './types'

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000'

export async function serverGet<T>(path: string): Promise<T> {
  const cookieStore = await cookies()
  const token = cookieStore.get('dse_access_token')?.value
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(`${API_BASE}${path}`, {
    headers,
    next: { revalidate: 0 },
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json() as Promise<T>
}

export const serverApi = {
  market: {
    indices: () => serverGet<MarketIndices>('/api/market/indices'),
    movers:  () => serverGet<MarketMovers>('/api/market/movers'),
    heatmap: () => serverGet<HeatmapItem[]>('/api/market/heatmap'),
    summary: () => serverGet<MarketSummary>('/api/market/summary'),
  },
}
```

- [ ] **Step 4: Create `frontend/lib/types.ts`** (shared type definitions used by both server and client)

```typescript
// frontend/lib/types.ts
export interface MarketIndices {
  dsex_value: number
  dsex_change_pct: number
  ds30_value: number
  ds30_change_pct: number
  dses_value: number
  dses_change_pct: number
  market_status: string
  advance: number
  decline: number
  unchanged: number
}

export interface TopMover {
  ticker: string
  name: string
  close: number
  change_pct: number
}

export interface MarketMovers {
  gainers: TopMover[]
  losers: TopMover[]
}

export interface MarketSummary {
  total_stocks: number
  advance: number
  decline: number
  unchanged: number
  total_volume: number | null
  total_value_bdt: number | null
  avg_change_pct: number | null
}

export interface HeatmapItem {
  ticker: string
  sector: string
  change_pct: number | null
  value_bdt: number | null
}
```

- [ ] **Step 5: Run tests**

```bash
cd frontend && npm test -- --testPathPattern=server-api
```

Expected: 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/lib/server-api.ts frontend/lib/types.ts frontend/__tests__/lib/server-api.test.ts
git commit -m "feat(frontend): server-side API helper + shared type definitions"
```

---

### Task 4: Frontend — `IndexCard` component

**Files:**
- Create: `frontend/components/market/IndexCard.tsx`
- Create: `frontend/__tests__/components/market/IndexCard.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/__tests__/components/market/IndexCard.test.tsx`:

```typescript
// frontend/__tests__/components/market/IndexCard.test.tsx
import { render, screen } from '@testing-library/react'
import IndexCard from '@/components/market/IndexCard'

test('renders label and formatted value', () => {
  render(<IndexCard label="DSEX" value={5330.89} changePct={1.27} />)
  expect(screen.getByText('DSEX')).toBeInTheDocument()
  expect(screen.getByText('5,330.89')).toBeInTheDocument()
})

test('shows green color class for positive change', () => {
  render(<IndexCard label="DSEX" value={5330.89} changePct={1.27} />)
  const pct = screen.getByText('+1.27%')
  expect(pct).toHaveClass('text-accent-green')
})

test('shows red color class for negative change', () => {
  render(<IndexCard label="DS30" value={2023.89} changePct={-0.50} />)
  const pct = screen.getByText('-0.50%')
  expect(pct).toHaveClass('text-accent-red')
})

test('shows zero change as green', () => {
  render(<IndexCard label="DSES" value={1078.43} changePct={0} />)
  const pct = screen.getByText('+0.00%')
  expect(pct).toHaveClass('text-accent-green')
})
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd frontend && npm test -- --testPathPattern=IndexCard
```

Expected: `Cannot find module '@/components/market/IndexCard'`

- [ ] **Step 3: Create `frontend/components/market/IndexCard.tsx`**

```tsx
// frontend/components/market/IndexCard.tsx
interface IndexCardProps {
  label: string
  value: number
  changePct: number
}

export default function IndexCard({ label, value, changePct }: IndexCardProps) {
  const isUp = changePct >= 0
  const sign = isUp ? '+' : ''
  return (
    <div className="bg-surface border border-border-custom rounded-lg px-5 py-4 min-w-[148px]">
      <p className="text-xs text-muted uppercase tracking-widest">{label}</p>
      <p className="text-xl font-semibold text-white mt-1">
        {value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </p>
      <p className={`text-sm font-medium mt-0.5 ${isUp ? 'text-accent-green' : 'text-accent-red'}`}>
        {sign}{changePct.toFixed(2)}%
      </p>
    </div>
  )
}
```

- [ ] **Step 4: Run tests**

```bash
cd frontend && npm test -- --testPathPattern=IndexCard
```

Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/components/market/IndexCard.tsx frontend/__tests__/components/market/IndexCard.test.tsx
git commit -m "feat(frontend): IndexCard component for DSEX/DS30/DSES display"
```

---

### Task 5: Frontend — `MoverStrip` component

**Files:**
- Create: `frontend/components/market/MoverStrip.tsx`
- Create: `frontend/__tests__/components/market/MoverStrip.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/__tests__/components/market/MoverStrip.test.tsx`:

```typescript
// frontend/__tests__/components/market/MoverStrip.test.tsx
import { render, screen } from '@testing-library/react'
import MoverStrip from '@/components/market/MoverStrip'
import type { TopMover } from '@/lib/types'

const gainers: TopMover[] = [
  { ticker: 'BRACBANK', name: 'BRAC Bank Ltd', close: 42.5, change_pct: 3.2 },
  { ticker: 'GP', name: 'Grameenphone Ltd', close: 320.0, change_pct: 1.8 },
]
const losers: TopMover[] = [
  { ticker: 'SQURPHARMA', name: 'Square Pharma', close: 210.0, change_pct: -2.1 },
]

test('renders gainers section heading', () => {
  render(<MoverStrip gainers={gainers} losers={losers} />)
  expect(screen.getByText('Top Gainers')).toBeInTheDocument()
  expect(screen.getByText('Top Losers')).toBeInTheDocument()
})

test('renders gainer tickers with positive change in green', () => {
  render(<MoverStrip gainers={gainers} losers={losers} />)
  expect(screen.getByText('BRACBANK')).toBeInTheDocument()
  const gpChange = screen.getByText('+1.80%')
  expect(gpChange).toHaveClass('text-accent-green')
})

test('renders loser tickers with negative change in red', () => {
  render(<MoverStrip gainers={gainers} losers={losers} />)
  expect(screen.getByText('SQURPHARMA')).toBeInTheDocument()
  const change = screen.getByText('-2.10%')
  expect(change).toHaveClass('text-accent-red')
})

test('renders empty lists without crashing', () => {
  render(<MoverStrip gainers={[]} losers={[]} />)
  expect(screen.getByText('Top Gainers')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd frontend && npm test -- --testPathPattern=MoverStrip
```

Expected: `Cannot find module '@/components/market/MoverStrip'`

- [ ] **Step 3: Create `frontend/components/market/MoverStrip.tsx`**

```tsx
// frontend/components/market/MoverStrip.tsx
import type { TopMover } from '@/lib/types'

interface MoverStripProps {
  gainers: TopMover[]
  losers: TopMover[]
}

function MoverItem({ mover }: { mover: TopMover }) {
  const isUp = mover.change_pct >= 0
  const sign = isUp ? '+' : ''
  return (
    <div className="flex items-center gap-3 px-4 py-2 bg-elevated rounded border border-border-custom min-w-[180px]">
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-white truncate">{mover.ticker}</p>
        <p className="text-xs text-muted truncate">{mover.name}</p>
      </div>
      <div className="text-right shrink-0">
        <p className="text-sm text-white">{mover.close.toFixed(2)}</p>
        <p className={`text-xs font-medium ${isUp ? 'text-accent-green' : 'text-accent-red'}`}>
          {sign}{mover.change_pct.toFixed(2)}%
        </p>
      </div>
    </div>
  )
}

export default function MoverStrip({ gainers, losers }: MoverStripProps) {
  return (
    <div className="mt-6 space-y-4">
      <div>
        <h2 className="text-xs font-semibold text-muted uppercase tracking-widest mb-2">Top Gainers</h2>
        <div className="flex gap-3 overflow-x-auto pb-2">
          {gainers.map((m) => (
            <MoverItem key={m.ticker} mover={m} />
          ))}
        </div>
      </div>
      <div>
        <h2 className="text-xs font-semibold text-muted uppercase tracking-widest mb-2">Top Losers</h2>
        <div className="flex gap-3 overflow-x-auto pb-2">
          {losers.map((m) => (
            <MoverItem key={m.ticker} mover={m} />
          ))}
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run tests**

```bash
cd frontend && npm test -- --testPathPattern=MoverStrip
```

Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/components/market/MoverStrip.tsx frontend/__tests__/components/market/MoverStrip.test.tsx
git commit -m "feat(frontend): MoverStrip component for top gainers/losers"
```

---

### Task 6: Frontend — `HeatmapGrid` component

**Files:**
- Create: `frontend/components/market/HeatmapGrid.tsx`
- Create: `frontend/__tests__/components/market/HeatmapGrid.test.tsx`

The heatmap receives `HeatmapItem[]` (ticker + sector + change_pct). Groups by sector, computes average `change_pct` per sector. Colors each sector square: green if avg > 0, red if avg < 0, neutral if 0 or null.

- [ ] **Step 1: Write the failing test**

Create `frontend/__tests__/components/market/HeatmapGrid.test.tsx`:

```typescript
// frontend/__tests__/components/market/HeatmapGrid.test.tsx
import { render, screen } from '@testing-library/react'
import HeatmapGrid from '@/components/market/HeatmapGrid'
import type { HeatmapItem } from '@/lib/types'

const items: HeatmapItem[] = [
  { ticker: 'BRACBANK', sector: 'Bank',  change_pct: 2.5,  value_bdt: 100 },
  { ticker: 'GP',       sector: 'Telecom', change_pct: 1.2, value_bdt: 200 },
  { ticker: 'SQURPHARMA', sector: 'Pharma', change_pct: -1.5, value_bdt: 80 },
  { ticker: 'RENATA',  sector: 'Pharma', change_pct: -0.5, value_bdt: 60 },
]

test('renders one square per sector', () => {
  render(<HeatmapGrid data={items} />)
  expect(screen.getByText('Bank')).toBeInTheDocument()
  expect(screen.getByText('Telecom')).toBeInTheDocument()
  expect(screen.getByText('Pharma')).toBeInTheDocument()
})

test('shows sector average change percentage', () => {
  render(<HeatmapGrid data={items} />)
  // Pharma avg: (-1.5 + -0.5) / 2 = -1.00%
  expect(screen.getByText('-1.00%')).toBeInTheDocument()
})

test('shows positive change in green sector square', () => {
  render(<HeatmapGrid data={items} />)
  const bankSquare = screen.getByText('Bank').closest('[data-testid="sector-square"]')
  expect(bankSquare?.className).toContain('accent-green')
})

test('shows negative change in red sector square', () => {
  render(<HeatmapGrid data={items} />)
  const pharmaSquare = screen.getByText('Pharma').closest('[data-testid="sector-square"]')
  expect(pharmaSquare?.className).toContain('accent-red')
})

test('renders empty data without crashing', () => {
  render(<HeatmapGrid data={[]} />)
  expect(screen.getByText('Sector Heatmap')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd frontend && npm test -- --testPathPattern=HeatmapGrid
```

Expected: `Cannot find module '@/components/market/HeatmapGrid'`

- [ ] **Step 3: Create `frontend/components/market/HeatmapGrid.tsx`**

```tsx
// frontend/components/market/HeatmapGrid.tsx
import type { HeatmapItem } from '@/lib/types'

interface SectorStat {
  sector: string
  avgChangePct: number
  tickerCount: number
}

function groupBySector(items: HeatmapItem[]): SectorStat[] {
  const map = new Map<string, { sum: number; count: number }>()
  for (const item of items) {
    if (!item.sector) continue
    const existing = map.get(item.sector) ?? { sum: 0, count: 0 }
    map.set(item.sector, {
      sum: existing.sum + (item.change_pct ?? 0),
      count: existing.count + 1,
    })
  }
  return Array.from(map.entries())
    .map(([sector, { sum, count }]) => ({
      sector,
      avgChangePct: count > 0 ? sum / count : 0,
      tickerCount: count,
    }))
    .sort((a, b) => b.avgChangePct - a.avgChangePct)
}

interface SectorSquareProps {
  stat: SectorStat
}

function SectorSquare({ stat }: SectorSquareProps) {
  const isUp = stat.avgChangePct >= 0
  const sign = isUp ? '+' : ''
  const bgClass = isUp ? 'bg-accent-green/20 border-accent-green/40' : 'bg-accent-red/20 border-accent-red/40'
  const textClass = isUp ? 'text-accent-green' : 'text-accent-red'
  return (
    <div
      data-testid="sector-square"
      className={`${bgClass} border rounded-lg p-4 flex flex-col justify-between min-h-[90px]`}
    >
      <p className="text-xs text-muted font-medium truncate">{stat.sector}</p>
      <div>
        <p className={`text-base font-semibold ${textClass}`}>
          {sign}{stat.avgChangePct.toFixed(2)}%
        </p>
        <p className="text-xs text-muted">{stat.tickerCount} stocks</p>
      </div>
    </div>
  )
}

export default function HeatmapGrid({ data }: { data: HeatmapItem[] }) {
  const sectors = groupBySector(data)
  return (
    <div className="mt-6">
      <h2 className="text-xs font-semibold text-muted uppercase tracking-widest mb-3">Sector Heatmap</h2>
      {sectors.length === 0 ? (
        <p className="text-muted text-sm">No data available.</p>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3">
          {sectors.map((s) => (
            <SectorSquare key={s.sector} stat={s} />
          ))}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run tests**

```bash
cd frontend && npm test -- --testPathPattern=HeatmapGrid
```

Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/components/market/HeatmapGrid.tsx frontend/__tests__/components/market/HeatmapGrid.test.tsx
git commit -m "feat(frontend): HeatmapGrid component — sector grouped with avg change%"
```

---

### Task 7: Frontend — Dashboard page (SSR)

**Files:**
- Replace: `frontend/app/(app)/dashboard/page.tsx`

No new tests needed — this is a Server Component that composes already-tested components. The integration is verified by `npm run build` passing.

- [ ] **Step 1: Replace the dashboard placeholder**

Replace the entire contents of `frontend/app/(app)/dashboard/page.tsx`:

```tsx
// frontend/app/(app)/dashboard/page.tsx
import { serverApi } from '@/lib/server-api'
import IndexCard from '@/components/market/IndexCard'
import MoverStrip from '@/components/market/MoverStrip'
import HeatmapGrid from '@/components/market/HeatmapGrid'

export default async function DashboardPage() {
  const [indices, movers, heatmap] = await Promise.allSettled([
    serverApi.market.indices(),
    serverApi.market.movers(),
    serverApi.market.heatmap(),
  ])

  const idx = indices.status === 'fulfilled' ? indices.value : null
  const mv  = movers.status === 'fulfilled'  ? movers.value  : null
  const hm  = heatmap.status === 'fulfilled' ? heatmap.value : []

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-white">Market Overview</h1>
        {idx && (
          <span className={`text-xs px-2 py-1 rounded font-medium ${
            idx.market_status === 'Open' ? 'bg-accent-green/20 text-accent-green' : 'bg-muted/20 text-muted'
          }`}>
            {idx.market_status}
          </span>
        )}
      </div>

      {idx ? (
        <div className="flex flex-wrap gap-4">
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
        </div>
      ) : (
        <p className="text-muted text-sm">Market data unavailable.</p>
      )}

      {mv && <MoverStrip gainers={mv.gainers} losers={mv.losers} />}
      <HeatmapGrid data={hm} />
    </div>
  )
}
```

- [ ] **Step 2: Verify build passes**

```bash
cd frontend && npm run build 2>&1 | tail -20
```

Expected: `✓ Compiled successfully` with no type errors

- [ ] **Step 3: Commit**

```bash
git add frontend/app/(app)/dashboard/page.tsx
git commit -m "feat(frontend): dashboard page SSR — index cards, movers, heatmap"
```

---

### Task 8: Frontend — TopBar SSE live strip

**Files:**
- Create: `frontend/components/layout/MarketStreamBar.tsx`
- Create: `frontend/__tests__/components/layout/MarketStreamBar.test.tsx`
- Replace: `frontend/components/layout/TopBar.tsx`

`MarketStreamBar` is `'use client'` — it opens an `EventSource` on `/api/market/stream` and displays the live DSEX value. `TopBar` stays a Server Component but renders `MarketStreamBar`.

- [ ] **Step 1: Write the failing test**

Create `frontend/__tests__/components/layout/MarketStreamBar.test.tsx`:

```typescript
// frontend/__tests__/components/layout/MarketStreamBar.test.tsx
import { render, screen, act } from '@testing-library/react'
import MarketStreamBar from '@/components/layout/MarketStreamBar'

type EventSourceListener = (event: MessageEvent) => void

class MockEventSource {
  url: string
  onmessage: EventSourceListener | null = null
  onerror: ((e: Event) => void) | null = null
  static instance: MockEventSource
  static OPEN = 1

  constructor(url: string) {
    this.url = url
    MockEventSource.instance = this
  }
  close() {}
}

beforeAll(() => {
  // @ts-expect-error – mock only
  global.EventSource = MockEventSource
})

test('shows loading state before first event', () => {
  render(<MarketStreamBar apiBase="http://localhost:8000" />)
  expect(screen.getByText('—')).toBeInTheDocument()
})

test('displays DSEX value from SSE event', async () => {
  render(<MarketStreamBar apiBase="http://localhost:8000" />)
  await act(async () => {
    MockEventSource.instance.onmessage?.({
      data: JSON.stringify({ dsex_value: 5330.89, dsex_change_pct: 1.27, market_status: 'Open' }),
    } as MessageEvent)
  })
  expect(screen.getByText('5,330.89')).toBeInTheDocument()
  expect(screen.getByText('+1.27%')).toBeInTheDocument()
})

test('shows error state on SSE error event', async () => {
  render(<MarketStreamBar apiBase="http://localhost:8000" />)
  await act(async () => {
    MockEventSource.instance.onerror?.(new Event('error'))
  })
  expect(screen.getByText('—')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd frontend && npm test -- --testPathPattern=MarketStreamBar
```

Expected: `Cannot find module '@/components/layout/MarketStreamBar'`

- [ ] **Step 3: Create `frontend/components/layout/MarketStreamBar.tsx`**

```tsx
// frontend/components/layout/MarketStreamBar.tsx
'use client'
import { useEffect, useState } from 'react'

interface IndexSnap {
  dsex_value: number
  dsex_change_pct: number
  market_status: string
}

export default function MarketStreamBar({ apiBase }: { apiBase?: string }) {
  const base = apiBase ?? (process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000')
  const [snap, setSnap] = useState<IndexSnap | null>(null)

  useEffect(() => {
    const es = new EventSource(`${base}/api/market/stream`)
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (!data.error) setSnap(data)
      } catch {
        // ignore parse errors
      }
    }
    es.onerror = () => setSnap(null)
    return () => es.close()
  }, [base])

  if (!snap) {
    return (
      <div className="flex items-center gap-2 text-sm">
        <span className="text-muted text-xs uppercase tracking-widest">DSEX</span>
        <span className="text-muted">—</span>
      </div>
    )
  }

  const isUp = snap.dsex_change_pct >= 0
  const sign = isUp ? '+' : ''
  return (
    <div className="flex items-center gap-3">
      <span className="text-muted text-xs uppercase tracking-widest">DSEX</span>
      <span className="text-white font-semibold text-sm">
        {snap.dsex_value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </span>
      <span className={`text-xs font-medium ${isUp ? 'text-accent-green' : 'text-accent-red'}`}>
        {sign}{snap.dsex_change_pct.toFixed(2)}%
      </span>
      <span className={`text-xs px-1.5 py-0.5 rounded ${
        snap.market_status === 'Open' ? 'bg-accent-green/20 text-accent-green' : 'bg-muted/20 text-muted'
      }`}>
        {snap.market_status}
      </span>
    </div>
  )
}
```

- [ ] **Step 4: Replace `frontend/components/layout/TopBar.tsx`**

```tsx
// frontend/components/layout/TopBar.tsx
import MarketStreamBar from './MarketStreamBar'

export default function TopBar() {
  return (
    <header className="h-14 border-b border-border bg-surface flex items-center px-6 gap-6">
      <span className="text-sm font-semibold text-white shrink-0">DSE Stock Intelligence</span>
      <div className="flex-1" />
      <MarketStreamBar />
    </header>
  )
}
```

- [ ] **Step 5: Run tests**

```bash
cd frontend && npm test -- --testPathPattern=MarketStreamBar
```

Expected: 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/components/layout/MarketStreamBar.tsx frontend/__tests__/components/layout/MarketStreamBar.test.tsx frontend/components/layout/TopBar.tsx
git commit -m "feat(frontend): TopBar SSE live DSEX strip via MarketStreamBar"
```

---

### Task 9: Frontend — Landing page

**Files:**
- Replace: `frontend/app/page.tsx`

No unit tests — landing page is pure JSX with no logic. Verified visually.

The landing page checks the session cookie server-side: authenticated users are redirected to `/dashboard`, unauthenticated users see the marketing page.

- [ ] **Step 1: Replace `frontend/app/page.tsx`**

```tsx
// frontend/app/page.tsx
import { cookies } from 'next/headers'
import { redirect } from 'next/navigation'
import Link from 'next/link'

export default async function RootPage() {
  const cookieStore = await cookies()
  const token = cookieStore.get('dse_access_token')
  if (token) redirect('/dashboard')

  return (
    <div className="min-h-screen bg-base text-white">
      {/* Nav */}
      <nav className="flex items-center justify-between px-8 py-5 border-b border-border-custom">
        <span className="text-lg font-bold text-white">DSE Intelligence</span>
        <div className="flex gap-3">
          <Link href="/login" className="text-sm text-muted hover:text-white transition-colors px-4 py-2">
            Log in
          </Link>
          <Link href="/register" className="text-sm bg-accent-blue text-white px-4 py-2 rounded-lg hover:opacity-90 transition-opacity">
            Get started
          </Link>
        </div>
      </nav>

      {/* Hero */}
      <section className="max-w-4xl mx-auto px-8 pt-24 pb-16 text-center">
        <h1 className="text-5xl font-bold leading-tight mb-5">
          AI-powered market intelligence<br />
          <span className="text-accent-blue">for Dhaka Stock Exchange</span>
        </h1>
        <p className="text-lg text-muted max-w-xl mx-auto mb-10">
          Real-time DSE market data, ML price predictions, fundamental analysis, and an AI chat analyst — all in one platform.
        </p>
        <div className="flex gap-4 justify-center">
          <Link href="/register" className="bg-accent-blue text-white px-8 py-3 rounded-lg font-semibold hover:opacity-90 transition-opacity">
            Start for free
          </Link>
          <Link href="/login" className="border border-border-custom text-muted px-8 py-3 rounded-lg hover:text-white hover:border-white transition-colors">
            Sign in
          </Link>
        </div>
      </section>

      {/* Features */}
      <section className="max-w-5xl mx-auto px-8 py-16">
        <h2 className="text-2xl font-bold text-center mb-10">Everything you need to invest smarter</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {[
            {
              title: 'Live Market Data',
              desc: 'Real-time DSEX, DS30, DSES indices. Top gainers, losers, and sector heatmap updated every 5 minutes.',
              color: 'text-accent-green',
            },
            {
              title: 'AI Stock Analyst',
              desc: 'Ask anything about DSE stocks in Bengali or English. Powered by Gemini with access to fundamentals and news.',
              color: 'text-accent-blue',
            },
            {
              title: 'ML Predictions',
              desc: '5/10/20 day price direction forecasts with bear/base/bull scenarios using LSTM and XGBoost models.',
              color: 'text-accent-gold',
            },
          ].map((f) => (
            <div key={f.title} className="bg-surface border border-border-custom rounded-xl p-6">
              <h3 className={`text-lg font-semibold mb-3 ${f.color}`}>{f.title}</h3>
              <p className="text-muted text-sm leading-relaxed">{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Pricing */}
      <section className="max-w-3xl mx-auto px-8 py-16">
        <h2 className="text-2xl font-bold text-center mb-10">Simple pricing</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Free */}
          <div className="bg-surface border border-border-custom rounded-xl p-8">
            <p className="text-muted text-sm uppercase tracking-widest mb-2">Free</p>
            <p className="text-4xl font-bold mb-1">৳0</p>
            <p className="text-muted text-sm mb-6">Forever free</p>
            <ul className="space-y-2 text-sm text-muted mb-8">
              {[
                '50 API calls / day',
                '3 AI chat queries / day',
                '3-year fundamentals',
                'Market dashboard',
                'Stock screener',
              ].map((item) => (
                <li key={item} className="flex gap-2">
                  <span className="text-accent-green">✓</span> {item}
                </li>
              ))}
            </ul>
            <Link href="/register" className="block text-center border border-border-custom text-white py-2.5 rounded-lg hover:bg-elevated transition-colors text-sm">
              Get started free
            </Link>
          </div>
          {/* Pro */}
          <div className="bg-surface border border-accent-blue rounded-xl p-8 relative">
            <span className="absolute top-4 right-4 text-xs bg-accent-blue text-white px-2 py-0.5 rounded">Popular</span>
            <p className="text-accent-blue text-sm uppercase tracking-widest mb-2">Pro</p>
            <p className="text-4xl font-bold mb-1">৳999</p>
            <p className="text-muted text-sm mb-6">per month</p>
            <ul className="space-y-2 text-sm text-muted mb-8">
              {[
                '1,000 API calls / day',
                '30 AI chat queries / day',
                '10-year fundamentals',
                'ML price predictions',
                'Unlimited portfolio',
                'PDF reports',
                'Portfolio risk analysis',
              ].map((item) => (
                <li key={item} className="flex gap-2">
                  <span className="text-accent-blue">✓</span> {item}
                </li>
              ))}
            </ul>
            <Link href="/register" className="block text-center bg-accent-blue text-white py-2.5 rounded-lg hover:opacity-90 transition-opacity text-sm font-semibold">
              Start Pro trial
            </Link>
          </div>
        </div>
      </section>

      {/* Disclaimer */}
      <footer className="border-t border-border-custom px-8 py-8 mt-8">
        <p className="text-xs text-muted text-center max-w-3xl mx-auto leading-relaxed">
          <strong className="text-white">Disclaimer:</strong> DSE Stock Intelligence provides market data and analysis tools for informational purposes only. Nothing on this platform constitutes investment advice. Market data may be delayed. Past performance of ML predictions does not guarantee future results. Always consult a qualified financial advisor before making investment decisions. This platform is not affiliated with the Dhaka Stock Exchange (DSE) or any regulatory authority.
        </p>
        <p className="text-xs text-muted text-center mt-4">© 2026 DSE Stock Intelligence. All rights reserved.</p>
      </footer>
    </div>
  )
}
```

- [ ] **Step 2: Verify build passes**

```bash
cd frontend && npm run build 2>&1 | tail -20
```

Expected: `✓ Compiled successfully` — no type errors, no SSR errors

- [ ] **Step 3: Commit**

```bash
git add frontend/app/page.tsx
git commit -m "feat(frontend): landing page with hero, features, pricing, disclaimer"
```

---

## Final Verification

After all tasks complete:

- [ ] Run full frontend test suite:
  ```bash
  cd frontend && npm test
  ```
  Expected: all tests pass (existing 19 + new tests)

- [ ] Run full backend test suite:
  ```bash
  pytest tests/unit/ -v
  ```
  Expected: all existing tests pass + new market tests pass

- [ ] Verify build:
  ```bash
  cd frontend && npm run build
  ```
  Expected: clean build, no errors

- [ ] Smoke test live (requires `docker compose up api` and `cd frontend && npm run dev`):
  - Visit `http://localhost:3000` — landing page visible
  - Click "Get started" → `/register`
  - Log in → `/dashboard`
  - TopBar shows DSEX strip updating
  - Dashboard shows index cards + movers + heatmap
