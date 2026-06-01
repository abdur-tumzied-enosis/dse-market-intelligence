# Stock Search Command Palette Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a global `Ctrl/Cmd+K` command palette to the consumer frontend that searches DSE companies by ticker or name and navigates to the selected stock's detail page.

**Architecture:** Backend `GET /api/stocks` gains a server-side `q` filter (ILIKE on ticker+name, escaped, length-capped). The frontend adds a typed `api.stocks.list()` client method, a shadcn `command`+`dialog`-based `CommandPalette` mounted once in the app layout, and a TopBar search-box trigger that opens it via a `window` custom event. Search is debounced and limited to the top 5 matches.

**Tech Stack:** FastAPI + asyncpg (backend), Next.js 16 + React 19 + shadcn/cmdk + Tailwind + lucide-react (frontend), pytest + jest/testing-library (tests).

**Spec:** `docs/superpowers/specs/2026-06-01-stock-search-command-palette-design.md`

---

## Concurrent-session warning (READ FIRST)

Another Claude session shares this working directory and has **uncommitted changes** in `frontend/package.json` (and `api/auth/jwt.py`, `docker-compose.yml`, `extraction/jobs.py`). HEAD may also be switched out from under you mid-task.

Rules for every commit in this plan:
- `git add` **only the exact paths listed in the task** — never `git add -A` / `git add .`.
- Before each commit run `git branch --show-current` and confirm it's the branch you expect; after each commit run `git log --oneline -1` to confirm it landed.
- **Task 2 modifies `frontend/package.json`** (adds `cmdk`), which the other session has edited. See that task's note before running it.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `api/routers/stocks.py` | `list_stocks` gains `q` filter | Modify (`list_stocks`, lines ~79–122) |
| `tests/unit/test_api_stocks.py` | backend `q` tests | Modify (append) |
| `frontend/lib/api.ts` | `api.stocks.list()` typed client | Modify (`api.stocks` object) |
| `frontend/__tests__/lib/api.test.ts` | `api.stocks.list` tests | Modify (append) |
| `frontend/components/ui/command.tsx` | shadcn command primitive | Create (shadcn CLI) |
| `frontend/components/ui/dialog.tsx` | shadcn dialog primitive | Create (shadcn CLI) |
| `frontend/components/layout/CommandPalette.tsx` | palette logic + UI | Create |
| `frontend/__tests__/components/layout/CommandPalette.test.tsx` | palette tests | Create |
| `frontend/components/layout/SearchTrigger.tsx` | TopBar trigger button | Create |
| `frontend/__tests__/components/layout/SearchTrigger.test.tsx` | trigger test | Create |
| `frontend/components/layout/TopBar.tsx` | render `<SearchTrigger />` | Modify |
| `frontend/app/(app)/layout.tsx` | mount `<CommandPalette />` once | Modify |

---

## Task 1: Backend `q` search filter

**Files:**
- Modify: `api/routers/stocks.py:79-122` (`list_stocks`)
- Test: `tests/unit/test_api_stocks.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_api_stocks.py`:

```python
def test_list_stocks_q_filters_by_ticker_or_name():
    pool = _pool_with(fetch_return=[], fetchval_return=0)
    with patch("api.routers.stocks._cache_get", return_value=None), \
         patch("api.routers.stocks._cache_set"):
        client = TestClient(_make_app(pool))
        resp = client.get("/api/stocks?q=gp")
    assert resp.status_code == 200
    conn = pool.acquire.return_value.__aenter__.return_value
    sql = conn.fetch.call_args.args[0]
    assert "ILIKE" in sql
    assert conn.fetch.call_args.args[1] == "%gp%"


def test_list_stocks_q_escapes_like_wildcards():
    pool = _pool_with(fetch_return=[], fetchval_return=0)
    with patch("api.routers.stocks._cache_get", return_value=None), \
         patch("api.routers.stocks._cache_set"):
        client = TestClient(_make_app(pool))
        resp = client.get("/api/stocks?q=50%25")  # %25 == literal '%'
    assert resp.status_code == 200
    conn = pool.acquire.return_value.__aenter__.return_value
    assert conn.fetch.call_args.args[1] == "%50\\%%"


def test_list_stocks_blank_q_applies_no_filter():
    pool = _pool_with(fetch_return=[], fetchval_return=0)
    with patch("api.routers.stocks._cache_get", return_value=None), \
         patch("api.routers.stocks._cache_set"):
        client = TestClient(_make_app(pool))
        resp = client.get("/api/stocks?q=%20%20")  # two spaces
    assert resp.status_code == 200
    conn = pool.acquire.return_value.__aenter__.return_value
    assert "ILIKE" not in conn.fetch.call_args.args[0]


def test_list_stocks_q_too_long_returns_422():
    pool = _pool_with(fetch_return=[], fetchval_return=0)
    with patch("api.routers.stocks._cache_get", return_value=None), \
         patch("api.routers.stocks._cache_set"):
        client = TestClient(_make_app(pool))
        resp = client.get("/api/stocks?q=" + "a" * 65)
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_api_stocks.py -k "q_filters or q_escapes or blank_q or q_too_long" -v`
Expected: FAIL (e.g. `ILIKE` not in SQL; 422 test fails because no `max_length` yet).

- [ ] **Step 3: Implement the `q` filter**

In `api/routers/stocks.py`, change the `list_stocks` signature (add `q` as the first param) and the cache key + WHERE building. Replace:

```python
async def list_stocks(
    sector: str | None = None,
    category: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    pool=Depends(get_db),
    _user=Depends(get_current_user),
):
    cache_key = f"cache:api:stocks:list:{sector}:{category}:{limit}:{offset}"
    cached = await _cache_get(cache_key)
    if cached:
        return cached

    where = "WHERE is_active = true"
    params: list = []
    if sector:
        params.append(sector)
        where += f" AND sector = ${len(params)}"
    if category:
        params.append(category)
        where += f" AND category = ${len(params)}"
```

with:

```python
async def list_stocks(
    q: str | None = Query(None, max_length=64),
    sector: str | None = None,
    category: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    pool=Depends(get_db),
    _user=Depends(get_current_user),
):
    q = q.strip() if q else None
    cache_key = f"cache:api:stocks:list:{q}:{sector}:{category}:{limit}:{offset}"
    cached = await _cache_get(cache_key)
    if cached:
        return cached

    where = "WHERE is_active = true"
    params: list = []
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append(f"%{escaped}%")
        n = len(params)
        where += f" AND (ticker ILIKE ${n} ESCAPE '\\' OR name ILIKE ${n} ESCAPE '\\')"
    if sector:
        params.append(sector)
        where += f" AND sector = ${len(params)}"
    if category:
        params.append(category)
        where += f" AND category = ${len(params)}"
```

Leave the rest of the function (the `fetch`, `fetchval`, result dict, `_cache_set`) unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_api_stocks.py -v`
Expected: PASS (all existing + 4 new).

- [ ] **Step 5: Lint + typecheck the changed file**

Run: `make lint && make typecheck`
Expected: no new errors in `api/routers/stocks.py`.

- [ ] **Step 6: Commit**

```bash
git add api/routers/stocks.py tests/unit/test_api_stocks.py
git commit -m "feat(api): add q search filter to GET /api/stocks"
git log --oneline -1
```

---

## Task 2: Add shadcn `command` + `dialog` components

**Files:**
- Create: `frontend/components/ui/command.tsx`, `frontend/components/ui/dialog.tsx`
- Modify: `frontend/package.json` (adds `cmdk` dependency)

> **Concurrent-session note:** The other session has uncommitted edits in
> `frontend/package.json`. Running the shadcn CLI will add `cmdk` to it. Before
> running, `git stash` is NOT safe (shared tree). Instead: run the CLI, then in
> Step 4 `git add` **only** `frontend/package.json` + `frontend/package-lock.json`
> + the two new `ui/` files, and visually confirm the diff to `package.json` is
> *only* the added `cmdk` line plus whatever the other session already had —
> commit both is acceptable since they coexist, but do not revert their line.

- [ ] **Step 1: Generate the components via shadcn CLI**

Run (from `frontend/`):

```bash
cd frontend && npx shadcn@latest add command dialog --yes
```

Expected: creates `components/ui/command.tsx` and `components/ui/dialog.tsx`, installs `cmdk` (and `@radix-ui/react-dialog` or the base-nova dialog dep) into `package.json`.

- [ ] **Step 2: Verify the generated files compile**

Run (from `frontend/`):

```bash
cd frontend && npx tsc --noEmit
```

Expected: no type errors. If the generated `dialog.tsx` references a primitive not installed (base-nova may use `@base-ui/react`), let the CLI's installed deps stand; do not hand-rewrite. Re-run `npm install` if a peer dep is missing.

- [ ] **Step 3: Confirm exports exist**

Run: `grep -E "export.*Command(Dialog|Input|List|Group|Item)" frontend/components/ui/command.tsx`
Expected: shows `CommandDialog`, `CommandInput`, `CommandList`, `CommandGroup`, `CommandItem` exports. (If `CommandDialog` is missing in this style's output, note it — Task 4 imports it; fall back to composing `Dialog` + `Command` there.)

- [ ] **Step 4: Commit**

```bash
git add frontend/components/ui/command.tsx frontend/components/ui/dialog.tsx frontend/package.json frontend/package-lock.json
git commit -m "chore(frontend): add shadcn command and dialog components"
git log --oneline -1
```

---

## Task 3: `api.stocks.list()` client method

**Files:**
- Modify: `frontend/lib/api.ts` (the `api.stocks` object)
- Test: `frontend/__tests__/lib/api.test.ts` (append)

- [ ] **Step 1: Write the failing tests**

Append to `frontend/__tests__/lib/api.test.ts`:

```typescript
describe('api.stocks.list', () => {
  it('builds query string and GETs stocks', async () => {
    const mock = { items: [], total: 0, limit: 5, offset: 0 }
    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true, status: 200, json: async () => mock,
    })
    const result = await api.stocks.list({ q: 'gp', limit: 5 })
    expect(global.fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/stocks?q=gp&limit=5',
      expect.objectContaining({}),
    )
    expect(result).toEqual(mock)
  })

  it('omits empty params', async () => {
    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true, status: 200, json: async () => ({ items: [], total: 0, limit: 50, offset: 0 }),
    })
    await api.stocks.list({})
    expect(global.fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/stocks',
      expect.anything(),
    )
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `cd frontend && npx jest __tests__/lib/api.test.ts -t "api.stocks.list"`
Expected: FAIL (`api.stocks.list is not a function`).

- [ ] **Step 3: Implement the method**

In `frontend/lib/api.ts`, replace the `stocks` object:

```typescript
  stocks: {
    prices: (ticker: string, params: string) =>
      get<import('./types').OHLCVResponse>(`/api/stocks/${ticker}/prices?${params}`),
    announcements: (ticker: string) =>
      get<import('./types').AnnouncementsResponse>(`/api/stocks/${ticker}/announcements`),
  },
```

with:

```typescript
  stocks: {
    list: (params: { q?: string; sector?: string; limit?: number; offset?: number } = {}) => {
      const qs = new URLSearchParams()
      if (params.q) qs.set('q', params.q)
      if (params.sector) qs.set('sector', params.sector)
      if (params.limit != null) qs.set('limit', String(params.limit))
      if (params.offset != null) qs.set('offset', String(params.offset))
      const suffix = qs.toString()
      return get<import('./types').PagedResponse<import('./types').StockListItem>>(
        `/api/stocks${suffix ? `?${suffix}` : ''}`,
      )
    },
    prices: (ticker: string, params: string) =>
      get<import('./types').OHLCVResponse>(`/api/stocks/${ticker}/prices?${params}`),
    announcements: (ticker: string) =>
      get<import('./types').AnnouncementsResponse>(`/api/stocks/${ticker}/announcements`),
  },
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `cd frontend && npx jest __tests__/lib/api.test.ts`
Expected: PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/api.ts frontend/__tests__/lib/api.test.ts
git commit -m "feat(frontend): add api.stocks.list query method"
git log --oneline -1
```

---

## Task 4: `CommandPalette` component

**Files:**
- Create: `frontend/components/layout/CommandPalette.tsx`
- Test: `frontend/__tests__/components/layout/CommandPalette.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/__tests__/components/layout/CommandPalette.test.tsx`:

```typescript
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import CommandPalette from '@/components/layout/CommandPalette'

const mockPush = jest.fn()
jest.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush }),
}))
jest.mock('@/lib/api', () => ({
  api: { stocks: { list: jest.fn() } },
}))
// Stub shadcn command so tests don't need cmdk/dialog portals
jest.mock('@/components/ui/command', () => ({
  CommandDialog: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
    open ? <div role="dialog">{children}</div> : null,
  CommandInput: ({ value, onValueChange, placeholder }: {
    value: string; onValueChange: (v: string) => void; placeholder?: string
  }) => (
    <input aria-label="search" placeholder={placeholder} value={value}
      onChange={(e) => onValueChange(e.target.value)} />
  ),
  CommandList: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  CommandGroup: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  CommandItem: ({ children, onSelect }: { children: React.ReactNode; onSelect: () => void }) => (
    <div role="option" onClick={onSelect}>{children}</div>
  ),
}))

import { api } from '@/lib/api'

beforeEach(() => {
  jest.clearAllMocks()
  jest.useFakeTimers()
})
afterEach(() => {
  jest.useRealTimers()
})

test('Ctrl+K opens the palette', () => {
  render(<CommandPalette />)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
  expect(screen.getByRole('dialog')).toBeInTheDocument()
})

test('open-command-palette event opens the palette', () => {
  render(<CommandPalette />)
  act(() => { window.dispatchEvent(new Event('open-command-palette')) })
  expect(screen.getByRole('dialog')).toBeInTheDocument()
})

test('typing fetches and renders results', async () => {
  ;(api.stocks.list as jest.Mock).mockResolvedValue({
    items: [{ ticker: 'GP', name: 'Grameenphone', sector: 'Telecom', category: 'A', market_cap_bdt: 1, is_active: true }],
    total: 1, limit: 5, offset: 0,
  })
  render(<CommandPalette />)
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
  fireEvent.change(screen.getByLabelText('search'), { target: { value: 'gp' } })
  await act(async () => { jest.advanceTimersByTime(250) })
  expect(api.stocks.list).toHaveBeenCalledWith({ q: 'gp', limit: 5 })
  expect(await screen.findByText('GP')).toBeInTheDocument()
})

test('selecting a result navigates to the stock page', async () => {
  ;(api.stocks.list as jest.Mock).mockResolvedValue({
    items: [{ ticker: 'GP', name: 'Grameenphone', sector: 'Telecom', category: 'A', market_cap_bdt: 1, is_active: true }],
    total: 1, limit: 5, offset: 0,
  })
  render(<CommandPalette />)
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
  fireEvent.change(screen.getByLabelText('search'), { target: { value: 'gp' } })
  await act(async () => { jest.advanceTimersByTime(250) })
  fireEvent.click(await screen.findByRole('option'))
  expect(mockPush).toHaveBeenCalledWith('/stocks/GP')
})

test('blank query does not call the API', async () => {
  render(<CommandPalette />)
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
  fireEvent.change(screen.getByLabelText('search'), { target: { value: '   ' } })
  await act(async () => { jest.advanceTimersByTime(250) })
  expect(api.stocks.list).not.toHaveBeenCalled()
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `cd frontend && npx jest __tests__/components/layout/CommandPalette.test.tsx`
Expected: FAIL (`Cannot find module '@/components/layout/CommandPalette'`).

- [ ] **Step 3: Implement the component**

Create `frontend/components/layout/CommandPalette.tsx`:

```typescript
'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  CommandDialog,
  CommandInput,
  CommandList,
  CommandGroup,
  CommandItem,
} from '@/components/ui/command'
import { api } from '@/lib/api'
import type { StockListItem } from '@/lib/types'

export default function CommandPalette() {
  const router = useRouter()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<StockListItem[]>([])
  const [loading, setLoading] = useState(false)
  const seq = useRef(0)

  // Open via Ctrl/Cmd+K or the `open-command-palette` window event.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen((o) => !o)
      }
    }
    function onOpenEvent() {
      setOpen(true)
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('open-command-palette', onOpenEvent)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('open-command-palette', onOpenEvent)
    }
  }, [])

  // Debounced server search; ignore out-of-order responses via a sequence ref.
  useEffect(() => {
    const q = query.trim()
    if (!q) {
      setResults([])
      setLoading(false)
      return
    }
    setLoading(true)
    const mySeq = ++seq.current
    const t = setTimeout(() => {
      api.stocks
        .list({ q, limit: 5 })
        .then((res) => {
          if (mySeq === seq.current) setResults(res.items)
        })
        .catch(() => {
          if (mySeq === seq.current) setResults([])
        })
        .finally(() => {
          if (mySeq === seq.current) setLoading(false)
        })
    }, 200)
    return () => clearTimeout(t)
  }, [query])

  const onSelect = useCallback(
    (ticker: string) => {
      setOpen(false)
      setQuery('')
      setResults([])
      router.push(`/stocks/${ticker}`)
    },
    [router],
  )

  const trimmed = query.trim()

  return (
    <CommandDialog open={open} onOpenChange={setOpen} shouldFilter={false}>
      <CommandInput
        placeholder="Search stocks by ticker or name…"
        value={query}
        onValueChange={setQuery}
      />
      <CommandList>
        {!trimmed && (
          <div className="py-6 text-center text-sm text-[#6b6b80]">Type to search stocks…</div>
        )}
        {trimmed && loading && (
          <div className="py-6 text-center text-sm text-[#6b6b80]">Searching…</div>
        )}
        {trimmed && !loading && results.length === 0 && (
          <div className="py-6 text-center text-sm text-[#6b6b80]">No stocks match.</div>
        )}
        {results.length > 0 && (
          <CommandGroup heading="Stocks">
            {results.map((s) => (
              <CommandItem key={s.ticker} value={s.ticker} onSelect={() => onSelect(s.ticker)}>
                <span className="font-mono font-bold text-[#4d9eff] mr-2">{s.ticker}</span>
                <span className="flex-1 truncate text-[#e8e8f0]">{s.name}</span>
                <span className="ml-2 text-[11px] font-mono text-[#6b6b80]">{s.sector}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}
      </CommandList>
    </CommandDialog>
  )
}
```

> If Task 2 Step 3 found no `CommandDialog` export, compose it here instead:
> import `Dialog`/`DialogContent` from `@/components/ui/dialog` and `Command`
> from `@/components/ui/command`, wrapping the same children. Keep the
> `shouldFilter={false}` on `Command`.

- [ ] **Step 4: Run the test to verify it passes**

Run (from `frontend/`): `cd frontend && npx jest __tests__/components/layout/CommandPalette.test.tsx`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/components/layout/CommandPalette.tsx frontend/__tests__/components/layout/CommandPalette.test.tsx
git commit -m "feat(frontend): add stock search command palette"
git log --oneline -1
```

---

## Task 5: TopBar trigger + mount palette in layout

**Files:**
- Create: `frontend/components/layout/SearchTrigger.tsx`
- Test: `frontend/__tests__/components/layout/SearchTrigger.test.tsx`
- Modify: `frontend/components/layout/TopBar.tsx`
- Modify: `frontend/app/(app)/layout.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/__tests__/components/layout/SearchTrigger.test.tsx`:

```typescript
import { render, screen, fireEvent } from '@testing-library/react'
import SearchTrigger from '@/components/layout/SearchTrigger'

test('clicking the trigger dispatches open-command-palette', () => {
  const spy = jest.fn()
  window.addEventListener('open-command-palette', spy)
  render(<SearchTrigger />)
  fireEvent.click(screen.getByRole('button'))
  expect(spy).toHaveBeenCalled()
  window.removeEventListener('open-command-palette', spy)
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `cd frontend && npx jest __tests__/components/layout/SearchTrigger.test.tsx`
Expected: FAIL (`Cannot find module '@/components/layout/SearchTrigger'`).

- [ ] **Step 3: Create the trigger component**

Create `frontend/components/layout/SearchTrigger.tsx`:

```typescript
'use client'

import { Search } from 'lucide-react'

export default function SearchTrigger() {
  return (
    <button
      type="button"
      onClick={() => window.dispatchEvent(new Event('open-command-palette'))}
      className="flex items-center gap-2 min-w-[220px] px-3 py-1.5 rounded-lg bg-[#1a1a24] border border-[#2a2a3a] text-sm text-[#6b6b80] hover:border-[#4d9eff]/50 hover:text-[#e8e8f0] transition-colors"
    >
      <Search className="w-3.5 h-3.5" />
      <span className="flex-1 text-left">Search stocks…</span>
      <kbd className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-[#0f0f18] border border-[#2a2a3a]">
        ⌘K
      </kbd>
    </button>
  )
}
```

> If `lucide-react` has no `Search` export in this version, replace the icon
> with a literal `<span aria-hidden>🔍</span>` — the button role/behaviour the
> test asserts is unchanged.

- [ ] **Step 4: Run the test to verify it passes**

Run (from `frontend/`): `cd frontend && npx jest __tests__/components/layout/SearchTrigger.test.tsx`
Expected: PASS.

- [ ] **Step 5: Render the trigger in TopBar**

In `frontend/components/layout/TopBar.tsx`, replace the file with:

```typescript
import MarketStreamBar from './MarketStreamBar'
import SearchTrigger from './SearchTrigger'

export default function TopBar() {
  return (
    <header className="h-14 border-b border-border bg-surface flex items-center px-6 gap-6">
      <span className="text-sm font-semibold text-white shrink-0">DSE Stock Intelligence</span>
      <SearchTrigger />
      <div className="flex-1" />
      <MarketStreamBar />
    </header>
  )
}
```

- [ ] **Step 6: Mount the palette in the app layout**

In `frontend/app/(app)/layout.tsx`, replace the file with:

```typescript
import Sidebar from '@/components/layout/Sidebar'
import TopBar from '@/components/layout/TopBar'
import CommandPalette from '@/components/layout/CommandPalette'

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen bg-base">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0">
        <TopBar />
        <main className="flex-1 p-6">{children}</main>
      </div>
      <CommandPalette />
    </div>
  )
}
```

- [ ] **Step 7: Run the full frontend suite + lint**

Run (from `frontend/`): `cd frontend && npx jest && npm run lint`
Expected: all tests PASS, lint clean.

- [ ] **Step 8: Commit**

```bash
git add frontend/components/layout/SearchTrigger.tsx frontend/__tests__/components/layout/SearchTrigger.test.tsx frontend/components/layout/TopBar.tsx "frontend/app/(app)/layout.tsx"
git commit -m "feat(frontend): wire search trigger and mount command palette"
git log --oneline -1
```

---

## Task 6: Manual verification

- [ ] **Step 1: Start backend + frontend**

Backend: `make up` (or run the API per project convention). Frontend: `cd frontend && npm run dev` (port 3000).

- [ ] **Step 2: Verify behavior**

In the browser at `http://localhost:3000` (logged in):
1. Press `Ctrl+K` (or `Cmd+K`) → palette opens, input focused.
2. Type `gp` → within ~200ms see up to 5 matches (ticker + name + sector).
3. Press `↓`/`↑` to highlight, `Enter` → navigates to `/stocks/<ticker>`.
4. Click the TopBar "Search stocks…" box → palette opens.
5. Type `%` → no broad match / no crash (literal match).
6. `Esc` → palette closes.

Expected: all six behave as described. Note any deviation before marking complete.

---

## Self-Review (completed by plan author)

- **Spec coverage:** backend `q`+escape+length → Task 1; `api.stocks.list` → Task 3; shadcn command/dialog → Task 2; palette (shortcut, event, debounce, stale-guard, states, navigate) → Task 4; TopBar trigger + layout mount → Task 5; backend `?q` tests → Task 1; FE palette test → Task 4. All spec sections mapped.
- **Type consistency:** `api.stocks.list(params)` signature identical in Task 3 impl and Task 4 usage (`{ q, limit: 5 }`); `StockListItem` fields match `frontend/lib/types.ts`; event name `open-command-palette` identical in CommandPalette (Task 4), SearchTrigger (Task 5), and tests.
- **Placeholders:** none — all steps carry full code/commands/expected output.
- **Out-of-scope** items from the spec (live prices, history, screener refactor, fuzzy match) are not introduced.
