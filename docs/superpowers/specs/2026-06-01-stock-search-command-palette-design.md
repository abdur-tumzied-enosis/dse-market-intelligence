# Stock Search Command Palette — Design

**Date:** 2026-06-01
**Status:** Approved (pending spec review)
**Scope:** `frontend/` (consumer FE), `api/routers/stocks.py`

## Summary

Add a global stock search to the consumer frontend. User presses `Ctrl/Cmd+K`
(or clicks a search-box trigger in the TopBar) to open a command palette,
types a ticker or company name, sees matching companies, and selects one to
navigate to its detail page (`/stocks/[ticker]`).

Built with the **shadcn `command` component** (cmdk-based) inside a shadcn
`dialog`. This is the first deliberate adoption of shadcn interactive
components in this FE; future features should prefer shadcn primitives over
hand-rolled equivalents.

## Decisions (locked)

| Decision | Choice |
|---|---|
| Search data source | Backend `?q` param on `GET /api/stocks` (server-side ILIKE) |
| Match scope | Ticker **and** company name |
| Result row content | Ticker + name + sector (no live price) |
| Palette UI | shadcn `command` + `dialog` (cmdk), **not** hand-rolled |
| Trigger | TopBar search-box-styled button + `Ctrl/Cmd+K` global shortcut |

## Rationale

- **Server-side `?q`, not client-side filter.** The palette is global (every
  app page via the shared layout). Fetching all ~350+ companies on every page
  load to filter in-browser is wasteful. The existing
  `stocks/page.tsx` screener fetches the full list because it shows the full
  list; the palette only needs the top N matches, so a debounced server query
  is leaner. (Backend already has the `companies` table and a paged
  `list_stocks` endpoint — only a `q` param is missing.)
- **shadcn command over hand-rolled.** Per user direction: standardize on
  shadcn for interactive UI. `command` gives accessible keyboard navigation,
  focus management, and dialog semantics for free.
- **cmdk client filtering disabled.** Matching happens server-side. The
  `command` list is populated from API results; cmdk's built-in fuzzy filter
  is turned off (`shouldFilter={false}`) so it does not re-filter what the
  server already matched.

## Architecture

### 1. Backend — `api/routers/stocks.py`

Extend `list_stocks` (the `GET /api/stocks` handler):

- Add param `q: str | None = None`.
- When `q` is set and non-blank, add to the WHERE clause:
  `AND (ticker ILIKE $n OR name ILIKE $n) ESCAPE '\'` with bound value
  `f"%{escaped}%"` (single bound param reused for both columns).
- Include the normalized `q` in `cache_key` so `?q=` responses cache
  independently.

**`q` input validation / hygiene** (the value is *bound* as a param —
asyncpg `$n` — so there is no SQL injection vector; `f"%...%"` builds only
the bound value, never SQL text. The following is correctness/perf hygiene,
not injection defense):
- `q = q.strip()`; treat empty-after-strip as no filter.
- Enforce a max length: `q: str | None = Query(None, max_length=64)` (FastAPI
  returns 422 on overflow — palette also caps input client-side).
- Escape ILIKE metacharacters before building the value so a literal `%`,
  `_`, or `\` is matched as itself, not as a wildcard:
  `escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")`
  paired with the `ESCAPE '\'` clause above.
- Existing `sector` / `category` filters, ordering, pagination, and response
  shape are unchanged.

No new endpoint — the screener page and the palette share `GET /api/stocks`.

### 2. FE API client — `frontend/lib/api.ts`

Add a typed list method to `api.stocks`:

```
list: (params: { q?: string; sector?: string; limit?: number; offset?: number }) =>
  get<PagedResponse<StockListItem>>(`/api/stocks?${query}`)
```

- Build the query string from defined params only (skip undefined/empty).
- `URLSearchParams` for encoding (ticker/name are ASCII but encode anyway).
- Palette calls `api.stocks.list({ q, limit: 8 })`.
- The existing `stocks/page.tsx` may optionally be refactored to use this
  method later; **out of scope** for this change (avoid touching the screener).

### 3. shadcn components — `frontend/components/ui/`

Add via the shadcn CLI (base-nova style, already configured in
`components.json`):

```
npx shadcn@latest add command dialog
```

This generates `components/ui/command.tsx` and `components/ui/dialog.tsx`
(plus the `cmdk` dependency in `package.json`). Do not hand-edit beyond what
the registry produces; theme tokens come from the existing `globals.css`.

> Next 16 caveat (`frontend/AGENTS.md`): this Next has breaking changes. Before
> writing client/route code, read the relevant guide under
> `node_modules/next/dist/docs/`. Verify the generated shadcn components
> compile against React 19 / Next 16 before building on them.

### 4. Palette component — `frontend/components/layout/CommandPalette.tsx`

`'use client'` component, mounted once in `app/(app)/layout.tsx`.

Responsibilities:
- **Open state.** Local `useState(false)`.
- **Global shortcut.** `useEffect` adds a `keydown` listener: `(e.metaKey ||
  e.ctrlKey) && e.key === 'k'` → `preventDefault()` + toggle open. `Escape`
  handled by the dialog itself.
- **Decoupled trigger.** Also listen for a `window` custom event
  `open-command-palette` → set open `true`. Lets the TopBar (a separate
  component) open the palette without a shared store or context.
- **Search.** Controlled input value; debounced ~200ms; on change call
  `api.stocks.list({ q, limit: 8 })`. Track `loading` and `results`.
  Ignore stale responses (guard with a request sequence/ref so a slow earlier
  request can't overwrite a newer one).
- **Render.** shadcn `CommandDialog` containing `CommandInput`,
  `CommandList`, `CommandEmpty` (states: idle "Type to search…", loading,
  "No stocks match"), and `CommandItem` per result.
  `shouldFilter={false}` on the Command root.
- **Item content.** Ticker (mono, accent), name (truncate), sector badge —
  styling consistent with `stocks/page.tsx`.
- **Select.** `onSelect` → `router.push('/stocks/' + ticker)` (Next
  `useRouter` from `next/navigation`), then close palette and clear query.
  cmdk provides arrow-key highlight + Enter-to-select natively.

### 5. TopBar trigger — `frontend/components/layout/TopBar.tsx`

In the existing `flex-1` spacer, add a button styled to look like a search
box (placeholder text + `⌘K` kbd hint), matching the dark theme. On click it
dispatches `window.dispatchEvent(new Event('open-command-palette'))`. It is a
visual affordance only; the palette owns all logic.

## Data Flow

```
User: Ctrl+K  ──► CommandPalette opens
User: types "gp" ──► debounce ──► api.stocks.list({q:"gp",limit:8})
                                        │
                                        ▼
                 GET /api/stocks?q=gp&limit=8 ──► ILIKE ticker/name ──► rows
                                        │
                                        ▼
              CommandList renders results ──► Enter/click ──► router.push(/stocks/GP)
```

## Error & Edge Handling

- Empty/whitespace `q`: do not call API; show idle hint. (Backend also
  treats blank `q` as no filter for safety.)
- `q` length capped client-side (input `maxLength={64}`) and server-side
  (`Query(max_length=64)`); over-long is rejected before hitting the DB.
- LIKE metacharacters (`%`, `_`, `\`) in `q` are escaped server-side and
  matched literally — no wildcard-injection / broad-match surprise.
- API error: show a single error line in `CommandList` (theme error color);
  do not crash the palette.
- Stale responses: sequence guard discards out-of-order results.
- No results: `CommandEmpty` "No stocks match".
- SSR: component is `'use client'`; `window`/event listeners only in
  `useEffect`.
- Auth: `api.ts` `get` already attaches the bearer token and handles 401
  refresh — no extra work.

## Testing

**Backend** (`tests/` stocks router suite):
- `?q=` matches by ticker (e.g. `q=GP`).
- `?q=` matches by company name substring.
- blank/absent `q` returns the unfiltered (sector/category) list.
- `q` combines with `sector` filter.
- `q` containing `%` / `_` is matched literally (e.g. `q=50%` does not match
  everything) — verifies ESCAPE handling.
- `q` over 64 chars returns 422.

**Frontend** (`frontend/__tests__/`, jest + testing-library):
- `Ctrl+K` opens the palette.
- `open-command-palette` event opens the palette.
- typing triggers a debounced `api.stocks.list` call (mocked) and renders
  result rows.
- selecting a row calls `router.push('/stocks/<ticker>')`.
- empty query shows idle state, no API call.

## Out of Scope (YAGNI)

- Live price/change in result rows.
- Recent-searches / history persistence.
- Searching anything other than companies (news, sectors).
- Refactoring `stocks/page.tsx` onto the new `api.stocks.list` method.
- Fuzzy/typo-tolerant matching (plain ILIKE substring is enough).

## Files Touched

| File | Change |
|---|---|
| `api/routers/stocks.py` | add `q` param + ILIKE + cache key |
| `frontend/lib/api.ts` | add `api.stocks.list()` |
| `frontend/components/ui/command.tsx` | new (shadcn add) |
| `frontend/components/ui/dialog.tsx` | new (shadcn add) |
| `frontend/components/layout/CommandPalette.tsx` | new |
| `frontend/components/layout/TopBar.tsx` | add trigger button |
| `frontend/app/(app)/layout.tsx` | mount `<CommandPalette />` |
| `package.json` (frontend) | `cmdk` dep (via shadcn) |
| backend stocks router tests | `?q=` cases |
| `frontend/__tests__/` | palette component test |

## Concurrent-session note

Another Claude session shares this working directory. Git operations for this
work must be scoped to only the files above (explicit `git add <paths>`, never
`git add -A`), and any commit must be verified to have landed without pulling
in the other session's changes.
