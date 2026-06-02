# Real DSE Market Status — Design

**Date:** 2026-06-02
**Branch:** feat-volume-profile-spec (new work)
**Status:** Approved

## Problem

Market status is faked from the clock (Sun–Thu 10:00–14:30 Asia/Dhaka) in five
places. When DSE is actually closed but the clock window is open (weekday
holidays, delayed open, early close, trading halt), the UI shows "Open"
incorrectly. We need the **real** status scraped from the DSE site, stored,
propagated to every consumer, and used to gate market-open-dependent jobs.

Current clock-based sources:
- `api/routers/market.py:100` `_market_status()` — drives `/market/indices`, SSE.
- `api/routers/stocks.py:504` — uses `_market_status` for live stock detail.
- `extraction/scheduler.py:66` `_market_is_open()` — gates `job_live_prices` write.
- `chat/prompt.py:64` `_is_market_open()` — LLM prompt context.
- `mgmt/agent/agent.py:131` `_is_market_open()` — ops-agent prompt context.

## Source signal

The DSE homepage `https://www.dsebd.org/index.php` (already scraped for indices
by `extraction/adapters/dse_direct/market_info.py`) carries a literal
**"Market Status: Open"** / **"Market Status: Closed"** node plus a
"Last update on <date> at <time>" stamp. Confirmed 2026-06-02 (page read
"Market Status: Closed", "Last update on Jun 02, 2026 at 2:30 PM").

## Decisions (locked)

| Decision | Choice |
|---|---|
| Storage | DB table `market_session` + Redis cache mirror |
| Scope | Replace clock in all 5 sites; clock = fallback only |
| Afternoon poll | Every 1 min, 14:00–15:00, until status reads Closed |
| Morning poll | Every 1 min, 10:00–10:15, until status reads Open |
| Scrape-failure fallback | Derive from clock, tag `source="clock"` |
| API/UI surface | Reuse `market_status` + new `status_source` field |

## Components

### 1. Status adapter — `extraction/adapters/dse_direct/market_status.py`

`DSEMarketStatusAdapter` (HTTP + BeautifulSoup, no JS). Fetches the homepage,
locates the `Market Status:` text node, returns one normalized record:

```
{ raw_label: "Market Status: Closed", status: "Closed",
  last_update_label: "Last update on Jun 02, 2026 at 2:30 PM",
  fetched_at: <utc>, source: "dse_direct_market_status" }
```

Normalization: raw label lower-cased; contains any of `open` / `continuous` /
`trading` → `"Open"`, else `"Closed"`. `raw_label` kept verbatim for audit.
`health_check()` returns True when the page loads and contains `Market Status`.

Registered as a priority-1 adapter in a new `market_status` DataStream in
`extraction/registry.py` (no fallback adapter; failure handled by the store's
clock fallback).

### 2. Storage

Migration `db/migrations/032_market_session.sql` — append-one-row-per-check
(transition history; latest row by `checked_at` is current):

```sql
CREATE TABLE IF NOT EXISTS market_session (
    id           BIGSERIAL PRIMARY KEY,
    session_date DATE        NOT NULL,
    status       TEXT        NOT NULL,           -- 'Open' | 'Closed'
    raw_label    TEXT,
    source       TEXT        NOT NULL,           -- 'dse_direct' | 'clock'
    checked_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_market_session_checked_at
    ON market_session (checked_at DESC);
```

Redis key `cache:market:session` mirrors the latest record:
`{status, source, checked_at, raw_label}` (no TTL; refreshed each scrape).

### 3. Status store — `extraction/market_status.py`

- `async refresh_market_status() -> dict` — fetch via the `market_status`
  stream; on success normalize + INSERT row + set Redis key (source
  `dse_direct`). On adapter failure: compute `clock_status(now)`, INSERT row
  with `source="clock"`, set Redis. Returns the record. Used by the scheduler
  jobs.
- `async get_market_status() -> dict` — read path for API: Redis → latest DB
  row → `clock_status(now)`. Returns `{status, source, checked_at}`.
- `get_market_status_sync() -> dict` — sync Redis read (redis-py sync client) →
  `clock_status(now)` fallback. For the two LLM prompt builders, which run in
  sync prompt-assembly code.
- `clock_status(now: datetime) -> str` — the existing Sun–Thu 10:00–14:30
  logic, extracted here as the single fallback implementation.

### 4. Scheduler jobs — `extraction/scheduler.py`

**`job_market_status_open`** — cron every 1 min, hour `10`, minute `0-15`,
Asia/Dhaka. Each fire calls `refresh_market_status()`. On a `Closed→Open`
transition, guarded by a per-day Redis flag `market:open_triggered:<date>`,
immediately `await job_live_prices()` (the only market-open-gated job) so the
first live pull does not wait for the next live-prices cron tick. If the market
never opens (holiday), the flag is never set and no live job fires.

**`job_market_status_close`** — cron every 1 min, hour `14`, minute `*`,
Asia/Dhaka (14:00–14:59). Each fire calls `refresh_market_status()`. Once
today's status reads `Closed`, later fires in the window short-circuit (skip the
scrape) via a per-day Redis flag `market:closed_confirmed:<date>`. The window
cron (hour 14, minute `*`) fires through 14:59 and naturally stops thereafter;
DSE's 14:30 close is inside the window.

Both registered in `_configure_production_mode`; added to the test-mode job map
with compressed intervals. Job count comment updated (13 → 15).

**`job_live_prices` change** — its write-gate switches from
`_market_is_open(now)` (clock) to `await get_market_status()` status == "Open"
(store, clock-fallback inside). `_market_is_open` is removed in favor of
`clock_status` in the store module; the live-prices cron still *fires* on the
clock schedule, but whether it *writes* now depends on real status.

### 5. API + UI propagation

- `api/routers/market.py`: `_market_status()` deleted; `_build_indices` awaits
  `get_market_status()` and emits both `market_status` and `status_source`. SSE
  generator already ends when `market_status != "Open"`, so the real close now
  drives stream teardown.
- `api/routers/stocks.py`: `_market_status()` import replaced with
  `get_market_status()`; live-detail responses carry `status_source`.
- `api/schemas/market.py` + `api/schemas/stocks.py`: add `status_source: str`.
- `chat/prompt.py` + `mgmt/agent/agent.py`: `_is_market_open()` →
  `get_market_status_sync()["status"] == "Open"`.

**Frontend:**
- `frontend/lib/types.ts`: add `status_source?: string` to the indices type.
- `frontend/app/(app)/dashboard/page.tsx` + `frontend/components/stocks/LivePrice.tsx`:
  when `status_source !== "dse_direct"`, append a small "(est.)" marker to the
  status badge so an operator can tell the value is a clock fallback, not
  scraped truth.

### 6. Config — `mgmt/config.py`

```
market_status_open_hour: int = 10          # morning window hour
market_status_open_minutes: str = "0-15"   # poll-until-open window
market_status_close_hour: int = 14         # afternoon window hour
market_status_close_minutes: str = "*"     # poll-until-closed window (14:00-14:59)
test_market_status_open_minutes: int = 3   # test-mode interval
test_market_status_close_minutes: int = 3
```

## Data flow

```
10:00–10:15 cron ─┐
14:00–14:59 cron ─┴→ refresh_market_status() ─→ market_session row + Redis key
                                              └→ (morning Closed→Open) job_live_prices()

GET /market/indices ─→ get_market_status() ─→ Redis → DB → clock
SSE /market/stream  ─→ (ends when status != Open)
LLM prompts         ─→ get_market_status_sync()
job_live_prices     ─→ get_market_status() gates the write
```

## Error handling

- Scrape failure (HTTP / markup change) → `clock_status` row, `source="clock"`,
  UI shows "(est.)". Logged at WARNING.
- Redis unavailable on read → fall through to DB → clock. On write → log, DB row
  still written.
- DB unavailable on `get_market_status()` read → clock fallback (never raises).

## Testing

- `test_market_status_adapter.py`: parse fixture HTML ("Market Status: Closed" /
  "...: Open"); normalization mapping; `health_check`.
- `test_market_status_store.py`: fallback chain (Redis hit, DB hit, clock
  fallback); `refresh` writes row + Redis on success and on scrape failure.
- Update `test_api_market_indices.py`, `test_api_market_stream.py`,
  `test_api_stocks_live.py`, `test_agent.py`, `test_live_prices_ingest.py` for
  async `get_market_status` / removed `_market_is_open` / new `status_source`.

## Out of scope (YAGNI)

- Intraday session phases (pre-open / post-close / run-off) — collapsed to
  binary Open/Closed.
- Backfilling historical sessions.
- Configurable per-exchange status (DSE only).
