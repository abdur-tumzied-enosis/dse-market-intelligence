# Real DSE Market Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace clock-guessed market status with the real Open/Closed status scraped from the DSE homepage, stored in Postgres + Redis, propagated to every consumer, and used to gate market-open jobs.

**Architecture:** A new `DSEMarketStatusAdapter` parses `Market Status: Open/Closed` from `dsebd.org/index.php`. A store module (`extraction/market_status.py`) refreshes that into a `market_session` table + Redis key and exposes async/sync readers with a clock fallback. Two cron windows (10:00–10:15 poll-until-open, 14:00–14:59 poll-until-closed) drive refresh; the morning window fires `job_live_prices` the moment the market opens. All five clock-based callers read the store instead.

**Tech Stack:** Python 3.12, FastAPI, asyncpg, APScheduler, redis-py (async + sync), BeautifulSoup/lxml, pytest; Next.js 16 / TypeScript frontend.

**Reference spec:** `docs/superpowers/specs/2026-06-02-market-status-real-design.md`

---

## File Structure

- Create `extraction/market_status.py` — pure helpers (`normalize_status`, `clock_status`) + store (`refresh_market_status`, `get_market_status`, `get_market_status_sync`). One responsibility: own the market-session truth.
- Create `extraction/adapters/dse_direct/market_status.py` — `DSEMarketStatusAdapter` (scrape + parse only).
- Create `db/migrations/032_market_session.sql` — table + index.
- Modify `extraction/registry.py` — register `market_status` stream.
- Modify `extraction/scheduler.py` — two new jobs, swap `job_live_prices` gate, drop `_market_is_open`, register jobs.
- Modify `mgmt/config.py` — window settings.
- Modify `api/routers/market.py`, `api/schemas/market.py` — read store, add `status_source`.
- Modify `api/routers/stocks.py`, `api/schemas/stocks.py` — read store, add `status_source`.
- Modify `chat/prompt.py`, `mgmt/agent/agent.py` — `_is_market_open` delegates to store.
- Modify `frontend/lib/types.ts`, `frontend/app/(app)/dashboard/page.tsx`, `frontend/components/stocks/LivePrice.tsx` — `(est.)` marker.
- Tests: create `tests/unit/test_market_status.py`, `tests/smoke/test_dse_market_status_smoke.py`; update `test_api_market_indices.py`, `test_api_market_stream.py`, `test_api_stocks_live.py`, `test_agent.py`, `test_live_prices_ingest.py`.

---

## Task 1: Pure helpers — `normalize_status` + `clock_status`

**Files:**
- Create: `extraction/market_status.py`
- Test: `tests/unit/test_market_status.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_market_status.py
import os
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from datetime import datetime

from extraction.market_status import clock_status, normalize_status
from extraction.normalizers import DHAKA_TZ


def test_normalize_status_maps_labels():
    assert normalize_status("Market Status: Open") == "Open"
    assert normalize_status("Market Status: Closed") == "Closed"
    assert normalize_status("market status : open") == "Open"
    assert normalize_status("Post-Close") == "Closed"
    assert normalize_status(None) == "Closed"
    assert normalize_status("") == "Closed"


def test_clock_status_matches_trading_window():
    # 2026-06-01 is a Monday (trading day)
    assert clock_status(datetime(2026, 6, 1, 11, 0, tzinfo=DHAKA_TZ)) == "Open"
    assert clock_status(datetime(2026, 6, 1, 10, 0, tzinfo=DHAKA_TZ)) == "Open"   # open boundary
    assert clock_status(datetime(2026, 6, 1, 14, 30, tzinfo=DHAKA_TZ)) == "Open"  # close boundary
    assert clock_status(datetime(2026, 6, 1, 9, 59, tzinfo=DHAKA_TZ)) == "Closed"
    assert clock_status(datetime(2026, 6, 1, 15, 0, tzinfo=DHAKA_TZ)) == "Closed"
    assert clock_status(datetime(2026, 6, 5, 11, 0, tzinfo=DHAKA_TZ)) == "Closed"  # Friday
    assert clock_status(datetime(2026, 6, 6, 11, 0, tzinfo=DHAKA_TZ)) == "Closed"  # Saturday
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_market_status.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'extraction.market_status'`.

- [ ] **Step 3: Write minimal implementation**

```python
# extraction/market_status.py
"""DSE market-session status: scrape → store (Postgres + Redis) → read.

Single source of truth for "is the market Open or Closed". Replaces the
clock-based guesses scattered across the API, scheduler, and LLM prompts. When
the scrape is unavailable, falls back to the Sun-Thu 10:00-14:30 clock and tags
the record source="clock" so callers can flag the value as estimated.
"""
from __future__ import annotations

import re
from datetime import datetime

from extraction.normalizers import DHAKA_TZ

_STATUS_RE = re.compile(r"market\s*status\s*:?\s*(open|closed)", re.IGNORECASE)


def normalize_status(raw_label: str | None) -> str:
    """Map a DSE status label to 'Open' or 'Closed'. Anything that is not an
    explicit 'open' reads as 'Closed' (fail-safe: we never invent an open
    market)."""
    if not raw_label:
        return "Closed"
    text = raw_label.lower()
    if "status" in text:
        text = text.split("status", 1)[1]
    text = text.strip(" :\t\r\n")
    return "Open" if text.startswith("open") else "Closed"


def clock_status(now: datetime) -> str:
    """Fallback status from the clock — DSE trades Sun-Thu, 10:00-14:30
    Asia/Dhaka. `now` must be tz-aware. Mon=0..Sun=6; Fri=4, Sat=5 closed."""
    if now.weekday() in (4, 5):
        return "Closed"
    minutes = now.hour * 60 + now.minute
    return "Open" if 600 <= minutes <= 870 else "Closed"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_market_status.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add extraction/market_status.py tests/unit/test_market_status.py
git commit -m "feat(market): clock_status + normalize_status helpers"
```

---

## Task 2: Status adapter — `DSEMarketStatusAdapter`

**Files:**
- Create: `extraction/adapters/dse_direct/market_status.py`
- Test: `tests/unit/test_market_status.py` (append)

- [ ] **Step 1: Write the failing test (append to the file)**

```python
# tests/unit/test_market_status.py  (append)
import pytest

from extraction.adapters.dse_direct.market_status import _parse_status

_PAGE_CLOSED = """
<html><body>
  <div class="topbar">Market Status: Closed</div>
  <div class="clock">Tue, 2 Jun, 26 2:31:52 PM Closed</div>
  <div class="LeftColHome">DSEX Index 5330.89</div>
</body></html>
"""

_PAGE_OPEN = """
<html><body><span>Market Status: Open</span></body></html>
"""


def test_parse_status_closed():
    rec = _parse_status(_PAGE_CLOSED)
    assert rec["status"] == "Closed"
    assert "Market Status" in rec["raw_label"]


def test_parse_status_open():
    assert _parse_status(_PAGE_OPEN)["status"] == "Open"


def test_parse_status_missing_raises():
    with pytest.raises(ValueError):
        _parse_status("<html><body>no status here</body></html>")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_market_status.py -k parse_status -v`
Expected: FAIL — `ModuleNotFoundError: extraction.adapters.dse_direct.market_status`.

- [ ] **Step 3: Write minimal implementation**

```python
# extraction/adapters/dse_direct/market_status.py
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd
from bs4 import BeautifulSoup

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.market_status import normalize_status

MARKET_STATUS_URL = "https://www.dsebd.org/index.php"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.dsebd.org/",
}

# The homepage carries a literal "Market Status: Open|Closed" string (confirmed
# 2026-06-02). Match it against the flattened page text so we don't depend on a
# fragile CSS path. The live clock line ("... 2:31 PM Closed") lacks the
# "Market Status" prefix, so it can't false-match.
_STATUS_RE = re.compile(r"market\s*status\s*:?\s*(open|closed)", re.IGNORECASE)


def _parse_status(html: str) -> dict[str, str]:
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    m = _STATUS_RE.search(text)
    if not m:
        raise ValueError("'Market Status' not found in page")
    return {"raw_label": m.group(0).strip(), "status": normalize_status(m.group(0))}


class DSEMarketStatusAdapter(BaseAdapter):
    """DSE official site — trading-session Open/Closed flag from the homepage.

    URL: https://www.dsebd.org/index.php
    Method: HTTP + BeautifulSoup (no JS). Priority 1, no fallback adapter —
    the market_status store handles scrape failure with a clock fallback.
    Output schema: status, raw_label, fetched_at, source.
    """
    name = "dse_direct_market_status"
    priority = 1
    timeout_seconds = 20

    def __init__(self, url: str = MARKET_STATUS_URL) -> None:
        self._url = url

    def normalize(self, raw: pd.DataFrame) -> pd.DataFrame:  # pragma: no cover - trivial
        return raw

    async def fetch(self, **kwargs: Any) -> AdapterResult:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, headers=HEADERS, follow_redirects=True
            ) as client:
                resp = await client.get(self._url)
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AdapterError(self.name, f"HTTP {exc.response.status_code}", retryable=True) from exc
        except Exception as exc:
            raise AdapterError(self.name, f"request failed: {exc}", retryable=True) from exc

        try:
            rec = _parse_status(resp.text)
        except Exception as exc:
            raise AdapterError(self.name, f"parse failed: {exc}", retryable=False) from exc

        fetched = datetime.now(timezone.utc)
        df = pd.DataFrame([{
            "status": rec["status"],
            "raw_label": rec["raw_label"],
            "fetched_at": fetched,
            "source": self.name,
        }])
        return AdapterResult(
            data=df, source_name=self.name, fetched_at=fetched,
            quality="ok", records=1, raw_sample=rec,
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10, headers=HEADERS, follow_redirects=True) as c:
                resp = await c.get(self._url)
                return resp.status_code == 200 and b"Market Status" in resp.content
        except Exception:
            return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_market_status.py -k parse_status -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add extraction/adapters/dse_direct/market_status.py tests/unit/test_market_status.py
git commit -m "feat(market): DSE market-status scrape adapter"
```

---

## Task 3: Register the `market_status` stream

**Files:**
- Modify: `extraction/registry.py:25` (import) and after the `market_indices` block (~line 67)

- [ ] **Step 1: Add the import**

After `from extraction.adapters.dse_direct.market_info import DSEDirectMarketInfoAdapter` (line 25) add:

```python
from extraction.adapters.dse_direct.market_status import DSEMarketStatusAdapter
```

- [ ] **Step 2: Register the stream**

After the `market_indices` stream block (the one ending at the `])` near line 67) insert:

```python
    # ------------------------------------------------------------------
    # Stream: market_status
    # Single adapter — DSE homepage "Market Status: Open|Closed" flag.
    # No fallback adapter: extraction.market_status falls back to the clock.
    # ------------------------------------------------------------------
    streams["market_status"] = DataStream(name="market_status", adapters=[
        DSEMarketStatusAdapter(),
    ])
```

- [ ] **Step 3: Verify the stream resolves**

Run: `python -c "from extraction.registry import STREAMS; print(STREAMS['market_status'].name)"`
Expected: prints `market_status`.

- [ ] **Step 4: Commit**

```bash
git add extraction/registry.py
git commit -m "feat(market): register market_status stream"
```

---

## Task 4: Migration — `market_session` table

**Files:**
- Create: `db/migrations/032_market_session.sql`

- [ ] **Step 1: Write the migration**

```sql
-- db/migrations/032_market_session.sql
-- Real DSE trading-session status, one row per check (transition history).
-- Latest row by checked_at is the current status.
CREATE TABLE IF NOT EXISTS market_session (
    id           BIGSERIAL PRIMARY KEY,
    session_date DATE        NOT NULL,
    status       TEXT        NOT NULL,   -- 'Open' | 'Closed'
    raw_label    TEXT,
    source       TEXT        NOT NULL,   -- 'dse_direct' | 'clock'
    checked_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_market_session_checked_at
    ON market_session (checked_at DESC);
```

- [ ] **Step 2: Apply the migration**

Run: `make migrate-local`
Expected: log line shows `032_market_session.sql` applied; re-running is a no-op (idempotent via `_migrations` tracking).

- [ ] **Step 3: Verify the table exists**

Run: `make db-shell` then `\d market_session` (or `psql ... -c "\d market_session"`).
Expected: columns `id, session_date, status, raw_label, source, checked_at`.

- [ ] **Step 4: Commit**

```bash
git add db/migrations/032_market_session.sql
git commit -m "feat(market): market_session table migration"
```

---

## Task 5: Store — `refresh_market_status`, `get_market_status`, `get_market_status_sync`

**Files:**
- Modify: `extraction/market_status.py`
- Test: `tests/unit/test_market_status.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
# tests/unit/test_market_status.py  (append)
from unittest.mock import AsyncMock, MagicMock, patch

import extraction.market_status as ms


@pytest.mark.asyncio
async def test_get_market_status_redis_hit():
    payload = {"status": "Open", "source": "dse_direct", "checked_at": "2026-06-02T11:00:00+06:00"}
    with patch.object(ms, "cache_get", AsyncMock(return_value=payload)):
        assert (await ms.get_market_status()) == payload


@pytest.mark.asyncio
async def test_get_market_status_db_fallback():
    row = {"status": "Closed", "source": "dse_direct",
           "checked_at": datetime(2026, 6, 2, 15, 0, tzinfo=DHAKA_TZ)}
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=row)
    with patch.object(ms, "cache_get", AsyncMock(return_value=None)), \
         patch.object(ms, "_get_pool", AsyncMock(return_value=pool)):
        result = await ms.get_market_status()
    assert result["status"] == "Closed"
    assert result["source"] == "dse_direct"


@pytest.mark.asyncio
async def test_get_market_status_clock_fallback():
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    with patch.object(ms, "cache_get", AsyncMock(return_value=None)), \
         patch.object(ms, "_get_pool", AsyncMock(return_value=pool)):
        result = await ms.get_market_status()
    assert result["status"] in ("Open", "Closed")
    assert result["source"] == "clock"


@pytest.mark.asyncio
async def test_refresh_uses_clock_on_scrape_failure():
    from extraction.base import AllAdaptersFailedError
    bad = MagicMock()
    bad.fetch = AsyncMock(side_effect=AllAdaptersFailedError("market_status", []))
    writes = []
    with patch.dict("extraction.market_status.STREAMS", {"market_status": bad}, clear=False), \
         patch.object(ms, "_write", AsyncMock(side_effect=lambda r: writes.append(r))):
        rec = await ms.refresh_market_status()
    assert rec["source"] == "clock"
    assert rec["status"] in ("Open", "Closed")
    assert writes and writes[0]["source"] == "clock"


@pytest.mark.asyncio
async def test_refresh_uses_scrape_when_ok():
    df = pd.DataFrame([{"status": "Open", "raw_label": "Market Status: Open"}])
    ok = MagicMock()
    ok.fetch = AsyncMock(return_value=MagicMock(data=df))
    with patch.dict("extraction.market_status.STREAMS", {"market_status": ok}, clear=False), \
         patch.object(ms, "_write", AsyncMock()):
        rec = await ms.refresh_market_status()
    assert rec["status"] == "Open"
    assert rec["source"] == "dse_direct"


def test_get_market_status_sync_clock_fallback():
    with patch.object(ms, "_sync_redis", MagicMock(side_effect=RuntimeError("no redis"))):
        result = ms.get_market_status_sync()
    assert result["status"] in ("Open", "Closed")
    assert result["source"] == "clock"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_market_status.py -k "get_market_status or refresh or sync" -v`
Expected: FAIL — `AttributeError: module 'extraction.market_status' has no attribute 'get_market_status'`.

- [ ] **Step 3: Add the store implementation (append to `extraction/market_status.py`)**

Add these imports at the top of the module (below the existing imports):

```python
import json
import logging

from extraction.registry import STREAMS
from mgmt.cache import cache_get, cache_set, get_redis

logger = logging.getLogger(__name__)

_REDIS_KEY = "cache:market:session"
_SESSION_TTL = 86_400  # 1 day — refreshed on every scrape
_sync_client = None  # lazy sync redis client for prompt builders
```

Append the store functions:

```python
async def _get_pool():
    """Indirection so tests can patch the DB pool without a live database."""
    from db.pool import get_pool
    return await get_pool()


def _sync_redis():
    """Lazily build a synchronous redis client for the (sync) LLM prompt builders."""
    global _sync_client
    if _sync_client is None:
        import redis  # redis-py sync client
        from mgmt.config import get_settings
        _sync_client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _sync_client


async def _write(record: dict) -> None:
    """Persist a session record: append a DB row and mirror to Redis. Both are
    best-effort — a refresh must never crash the scheduler job."""
    from datetime import date as _date
    try:
        pool = await _get_pool()
        await pool.execute(
            """
            INSERT INTO market_session (session_date, status, raw_label, source, checked_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            _date.fromisoformat(record["session_date"]),
            record["status"],
            record["raw_label"],
            record["source"],
            datetime.fromisoformat(record["checked_at"]),
        )
    except Exception as exc:
        logger.warning("market_status: DB write failed error=%s", exc)
    try:
        await cache_set(_REDIS_KEY, record, ttl=_SESSION_TTL)
    except Exception as exc:
        logger.warning("market_status: redis write failed error=%s", exc)


async def refresh_market_status() -> dict:
    """Scrape DSE status → normalize → persist (DB + Redis) → return the record.
    On any scrape/parse failure, derive the status from the clock and tag
    source='clock'."""
    now = datetime.now(DHAKA_TZ)
    try:
        result = await STREAMS["market_status"].fetch()
        rec = result.data.to_dict("records")[0]
        status = rec["status"]
        raw_label = rec.get("raw_label")
        source = "dse_direct"
    except Exception as exc:
        logger.warning("market_status: refresh failed, clock fallback error=%s", exc)
        status = clock_status(now)
        raw_label = None
        source = "clock"
    record = {
        "status": status,
        "source": source,
        "raw_label": raw_label,
        "session_date": now.date().isoformat(),
        "checked_at": now.isoformat(),
    }
    await _write(record)
    return record


async def get_market_status() -> dict:
    """Read path: Redis → latest DB row → clock fallback. Never raises."""
    try:
        cached = await cache_get(_REDIS_KEY)
        if cached:
            return cached
    except Exception as exc:
        logger.debug("market_status: redis read failed error=%s", exc)
    try:
        pool = await _get_pool()
        row = await pool.fetchrow(
            "SELECT status, source, checked_at FROM market_session ORDER BY checked_at DESC LIMIT 1"
        )
        if row:
            checked = row["checked_at"]
            return {
                "status": row["status"],
                "source": row["source"],
                "checked_at": checked.isoformat() if hasattr(checked, "isoformat") else checked,
            }
    except Exception as exc:
        logger.debug("market_status: db read failed error=%s", exc)
    now = datetime.now(DHAKA_TZ)
    return {"status": clock_status(now), "source": "clock", "checked_at": now.isoformat()}


def get_market_status_sync() -> dict:
    """Synchronous read for the LLM prompt builders: Redis → clock fallback."""
    try:
        raw = _sync_redis().get(_REDIS_KEY)
        if raw:
            return json.loads(raw)
    except Exception as exc:
        logger.debug("market_status: sync redis read failed error=%s", exc)
    now = datetime.now(DHAKA_TZ)
    return {"status": clock_status(now), "source": "clock", "checked_at": now.isoformat()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_market_status.py -v`
Expected: PASS (all tests in file).

- [ ] **Step 5: Commit**

```bash
git add extraction/market_status.py tests/unit/test_market_status.py
git commit -m "feat(market): market-session store (refresh + async/sync readers)"
```

---

## Task 6: Config — status job windows

**Files:**
- Modify: `mgmt/config.py` (after the `live_prices_minutes` line, ~line 64)

- [ ] **Step 1: Add settings**

After `live_prices_minutes: str = "*/2"` (line 64) insert:

```python
    # market_status: scrape DSE Open/Closed. Morning window polls until open,
    # afternoon window polls until closed. day_of_week reuses live_prices_market_days.
    market_status_open_hour: int = 10
    market_status_open_minutes: str = "0-15"     # 10:00–10:15, every minute
    market_status_close_hour: int = 14
    market_status_close_minutes: str = "*"        # 14:00–14:59, every minute
    test_market_status_minutes: int = 3           # test-mode interval
```

- [ ] **Step 2: Verify config loads**

Run: `python -c "from mgmt.config import get_settings; s=get_settings(); print(s.market_status_open_minutes, s.market_status_close_hour)"`
Expected: prints `0-15 14`.

- [ ] **Step 3: Commit**

```bash
git add mgmt/config.py
git commit -m "feat(market): config for market-status poll windows"
```

---

## Task 7: Scheduler — status jobs + gate swap + registration

**Files:**
- Modify: `extraction/scheduler.py`
- Test: `tests/unit/test_market_status.py` (append job tests); `tests/unit/test_live_prices_ingest.py` (repoint clock test)

- [ ] **Step 1: Write failing tests for the jobs (append to `tests/unit/test_market_status.py`)**

```python
# tests/unit/test_market_status.py  (append)
import extraction.scheduler as sched


@pytest.mark.asyncio
async def test_job_market_status_open_triggers_live_once():
    """When the morning poll sees Open, job_live_prices fires once (Redis flag guards repeats)."""
    flag_store = {}
    redis = MagicMock()
    redis.get = AsyncMock(side_effect=lambda k: flag_store.get(k))
    redis.set = AsyncMock(side_effect=lambda k, v, ex=None: flag_store.__setitem__(k, v))
    rec = {"status": "Open", "source": "dse_direct", "session_date": "2026-06-02",
           "checked_at": "2026-06-02T10:01:00+06:00"}
    with patch.object(sched, "refresh_market_status", AsyncMock(return_value=rec)), \
         patch.object(sched, "get_redis", AsyncMock(return_value=redis)), \
         patch.object(sched, "job_live_prices", AsyncMock()) as live:
        await sched.job_market_status_open()
        await sched.job_market_status_open()  # second fire — flag set, no re-trigger
    assert live.await_count == 1


@pytest.mark.asyncio
async def test_job_market_status_open_no_trigger_when_closed():
    rec = {"status": "Closed", "source": "dse_direct", "session_date": "2026-06-02",
           "checked_at": "2026-06-02T10:01:00+06:00"}
    redis = MagicMock(); redis.get = AsyncMock(return_value=None); redis.set = AsyncMock()
    with patch.object(sched, "refresh_market_status", AsyncMock(return_value=rec)), \
         patch.object(sched, "get_redis", AsyncMock(return_value=redis)), \
         patch.object(sched, "job_live_prices", AsyncMock()) as live:
        await sched.job_market_status_open()
    assert live.await_count == 0


@pytest.mark.asyncio
async def test_job_market_status_close_short_circuits_after_closed():
    rec = {"status": "Closed", "source": "dse_direct", "session_date": "2026-06-02",
           "checked_at": "2026-06-02T14:31:00+06:00"}
    flag_store = {}
    redis = MagicMock()
    redis.get = AsyncMock(side_effect=lambda k: flag_store.get(k))
    redis.set = AsyncMock(side_effect=lambda k, v, ex=None: flag_store.__setitem__(k, v))
    refresh = AsyncMock(return_value=rec)
    with patch.object(sched, "refresh_market_status", refresh), \
         patch.object(sched, "get_redis", AsyncMock(return_value=redis)):
        await sched.job_market_status_close()  # scrapes, sets flag
        await sched.job_market_status_close()  # flag set → short-circuit, no scrape
    assert refresh.await_count == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/unit/test_market_status.py -k "job_market_status" -v`
Expected: FAIL — `AttributeError: module 'extraction.scheduler' has no attribute 'job_market_status_open'`.

- [ ] **Step 3: Add imports + jobs to `extraction/scheduler.py`**

At the top of the module (after the existing imports, ~line 21) add:

```python
from extraction.market_status import get_market_status, refresh_market_status
from mgmt.cache import get_redis
```

Replace the `_market_is_open` function (lines 66-72) with nothing — delete it (its clock logic now lives in `extraction.market_status.clock_status`).

In `job_live_prices`, replace the gate block (lines 275-279):

```python
    now = datetime.now(BD_TZ)
    if not _market_is_open(now):
        logger.info("job_live_prices: market closed — skipping write")
        await _invalidate_live_caches()
        return
```

with:

```python
    now = datetime.now(BD_TZ)
    status = (await get_market_status())["status"]
    if status != "Open":
        logger.info("job_live_prices: market not open (status=%s) — skipping write", status)
        await _invalidate_live_caches()
        return
```

Add the two new job functions just before the `# ── Scheduler Configuration ──` banner (~line 693):

```python
async def job_market_status_open() -> None:
    """Morning poll-until-open (cron 10:00-10:15, every minute). Refresh the
    real DSE status; on the first Closed→Open transition of the day, fire
    job_live_prices immediately so the first live pull doesn't wait for the next
    live-prices tick. A per-day Redis flag guards against re-triggering."""
    rec = await refresh_market_status()
    if rec["status"] != "Open":
        return
    flag = f"market:open_triggered:{rec['session_date']}"
    redis = await get_redis()
    if await redis.get(flag):
        return
    await redis.set(flag, "1", ex=86_400)
    logger.info("job_market_status_open: market open — triggering live_prices")
    await job_live_prices()


async def job_market_status_close() -> None:
    """Afternoon poll-until-closed (cron 14:00-14:59, every minute). Refresh the
    real DSE status; once today's status reads Closed, set a per-day Redis flag
    so later fires in the window short-circuit (no extra scrapes)."""
    today = datetime.now(BD_TZ).date().isoformat()
    flag = f"market:closed_confirmed:{today}"
    redis = await get_redis()
    if await redis.get(flag):
        return
    rec = await refresh_market_status()
    if rec["status"] == "Closed":
        await redis.set(flag, "1", ex=86_400)
        logger.info("job_market_status_close: market closed confirmed")
```

- [ ] **Step 4: Register the jobs (production mode)**

In `_configure_production_mode`, after the `job_live_prices` `add_job` block (ends ~line 718) insert:

```python
    scheduler.add_job(
        job_market_status_open,
        trigger="cron",
        day_of_week=cfg.live_prices_market_days,
        hour=cfg.market_status_open_hour,
        minute=cfg.market_status_open_minutes,
        id="market_status_open",
        replace_existing=True,
        misfire_grace_time=120,
    )

    scheduler.add_job(
        job_market_status_close,
        trigger="cron",
        day_of_week=cfg.live_prices_market_days,
        hour=cfg.market_status_close_hour,
        minute=cfg.market_status_close_minutes,
        id="market_status_close",
        replace_existing=True,
        misfire_grace_time=120,
    )
```

Change the final log line in `_configure_production_mode` from `"scheduler: all 13 jobs registered (production mode)"` to `"scheduler: all 15 jobs registered (production mode)"`.

- [ ] **Step 5: Register the jobs (test mode)**

In `_configure_test_mode`, add to the `job_map` list (after the `job_live_prices` entry):

```python
        (job_market_status_open,  "market_status_open",  cfg.test_market_status_minutes),
        (job_market_status_close, "market_status_close", cfg.test_market_status_minutes),
```

Change the test-mode warning `"...all 13 intervals compressed..."` to `"...all 15 intervals compressed..."`.

- [ ] **Step 6: Repoint the clock test in `tests/unit/test_live_prices_ingest.py`**

Replace the import `from extraction.scheduler import (... _market_is_open ...)` — remove `_market_is_open` from that import list — and add at the top of the file:

```python
from extraction.market_status import clock_status
```

Replace the body of `test_market_is_open_weekday_and_hours` (lines 93-101) with calls to `clock_status`, asserting on the returned string:

```python
def test_market_is_open_weekday_and_hours():
    assert clock_status(BD.localize(datetime(2026, 6, 1, 11, 0))) == "Open"   # Mon 11:00
    assert clock_status(BD.localize(datetime(2026, 6, 1, 15, 0))) == "Closed" # Mon 15:00
    assert clock_status(BD.localize(datetime(2026, 6, 5, 11, 0))) == "Closed" # Fri
    assert clock_status(BD.localize(datetime(2026, 6, 1, 10, 0))) == "Open"   # open boundary
    assert clock_status(BD.localize(datetime(2026, 6, 1, 9, 59))) == "Closed"
    assert clock_status(BD.localize(datetime(2026, 6, 1, 14, 30))) == "Open"  # close boundary
    assert clock_status(BD.localize(datetime(2026, 6, 1, 14, 31))) == "Closed"
    assert clock_status(BD.localize(datetime(2026, 6, 6, 11, 0))) == "Closed" # Sat
```

- [ ] **Step 7: Run the tests**

Run: `pytest tests/unit/test_market_status.py tests/unit/test_live_prices_ingest.py -v`
Expected: PASS. Also confirm the scheduler imports cleanly:
Run: `python -c "import extraction.scheduler"`
Expected: no error.

- [ ] **Step 8: Commit**

```bash
git add extraction/scheduler.py tests/unit/test_market_status.py tests/unit/test_live_prices_ingest.py
git commit -m "feat(market): status poll jobs + real-status gate for live_prices"
```

---

## Task 8: API — market.py + schema

**Files:**
- Modify: `api/routers/market.py`, `api/schemas/market.py`
- Test: `tests/unit/test_api_market_indices.py`, `tests/unit/test_api_market_stream.py`

- [ ] **Step 1: Update the indices test (`tests/unit/test_api_market_indices.py`)**

Change the import line 13 from:

```python
from api.routers.market import _build_indices, _market_status
```

to:

```python
from unittest.mock import AsyncMock
from api.routers.market import _build_indices
```

In `test_build_indices_maps_from_registry` and `test_build_indices_partial_when_live_breadth_fails`, wrap the `_build_indices()` call so the status read is stubbed (add the patch alongside the existing `patch.dict`):

```python
    with patch.dict("api.routers.market.STREAMS", streams), \
         patch("api.routers.market.get_market_status",
               AsyncMock(return_value={"status": "Closed", "source": "clock"})):
        result = await _build_indices()
```

Add an assertion in `test_build_indices_maps_from_registry`:

```python
    assert result["status_source"] == "clock"
```

Delete `test_market_status_clock` (lines 67-73) — clock behavior is now covered by `tests/unit/test_market_status.py::test_clock_status_matches_trading_window`. Remove the now-unused `DHAKA_TZ` / `datetime` imports if they are no longer referenced.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/unit/test_api_market_indices.py -v`
Expected: FAIL — `ImportError: cannot import name '_build_indices'`-style failure is gone, but `KeyError: 'status_source'` (field not yet emitted).

- [ ] **Step 3: Update `api/routers/market.py`**

Delete the `_market_status` function (lines 100-108). Add the import near the other `extraction` imports (~line 17):

```python
from extraction.market_status import get_market_status
```

In `_build_indices`, replace the `"market_status": _market_status(),` line (225) with both fields, fetching the session once near the top of the function (just after `idx_map = ...`, line 197):

```python
    session = await get_market_status()
```

and in the returned dict replace `"market_status": _market_status(),` with:

```python
        "market_status": session["status"],
        "status_source": session["source"],
```

- [ ] **Step 4: Add the schema field (`api/schemas/market.py`)**

In `MarketIndices` (after `market_status: str`, line 41) add:

```python
    status_source: str
```

- [ ] **Step 5: Confirm the SSE close test still passes**

`tests/unit/test_api_market_stream.py` feeds `FAKE_INDICES` dicts through `_generate_market_events` with a stubbed `get_indices_fn`, so it does not touch `get_market_status`. Add `"status_source": "dse_direct"` to the `FAKE_INDICES` dict (line 17) for realism. The close test (`market_status: "Closed"`) is unchanged.

- [ ] **Step 6: Run the tests**

Run: `pytest tests/unit/test_api_market_indices.py tests/unit/test_api_market_stream.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add api/routers/market.py api/schemas/market.py tests/unit/test_api_market_indices.py tests/unit/test_api_market_stream.py
git commit -m "feat(market): /market/indices reads real status + status_source"
```

---

## Task 9: API — stocks.py + schema

**Files:**
- Modify: `api/routers/stocks.py`, `api/schemas/stocks.py`
- Test: `tests/unit/test_api_stocks_live.py`

- [ ] **Step 1: Update the live-price test (`tests/unit/test_api_stocks_live.py`)**

The four tests patch `api.routers.stocks._market_status`. Replace each such patch with a patch of the new async reader. Example for the "Open" case:

```python
    with patch("api.routers.stocks._live_snapshot", AsyncMock(return_value=SNAPSHOT)), \
         patch("api.routers.stocks.get_market_status",
               AsyncMock(return_value={"status": "Open", "source": "dse_direct"})):
```

Apply the same substitution (`_market_status` → `get_market_status` returning `{"status": ..., "source": ...}`) in all four test bodies, keeping each test's existing Open/Closed value. Add to one assertion block:

```python
    assert body["status_source"] == "dse_direct"
```

(Ensure `AsyncMock` is imported in this test file.)

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/unit/test_api_stocks_live.py -v`
Expected: FAIL — patched target `_market_status` no longer exists / `KeyError: 'status_source'`.

- [ ] **Step 3: Update `api/routers/stocks.py`**

Change the import (line 16) from:

```python
from api.routers.market import _market_status
```

to:

```python
from extraction.market_status import get_market_status
```

Replace `status = _market_status()` (line 504) with:

```python
    session = await get_market_status()
    status = session["status"]
```

In both returned dicts (the `available: True` dict ending ~line 533 and the `available: False` dict ending ~line 542), add after the `"market_status": status,` line:

```python
            "status_source": session["source"],
```

- [ ] **Step 4: Add the schema field (`api/schemas/stocks.py`)**

In the `LivePrice` schema, after `market_status: str` (line 162) add:

```python
    status_source: str
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/unit/test_api_stocks_live.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/routers/stocks.py api/schemas/stocks.py tests/unit/test_api_stocks_live.py
git commit -m "feat(market): /stocks/{ticker}/live reads real status + status_source"
```

---

## Task 10: LLM prompt builders read the store

**Files:**
- Modify: `chat/prompt.py`, `mgmt/agent/agent.py`
- Test: `tests/unit/test_agent.py`

- [ ] **Step 1: Update `tests/unit/test_agent.py`**

The import (line 23) pulls `_is_market_open` from `mgmt.agent.agent`. The four assertions (lines 148-172) currently patch the module's `datetime`. Repoint them to patch the store reader instead. Replace each `_is_market_open()` assertion group with a stub of the sync reader:

```python
    with patch("mgmt.agent.agent.get_market_status_sync",
               return_value={"status": "Open", "source": "dse_direct"}):
        assert _is_market_open() is True

    with patch("mgmt.agent.agent.get_market_status_sync",
               return_value={"status": "Closed", "source": "clock"}):
        assert _is_market_open() is False
```

(Collapse the four datetime-based cases into these two; remove the now-unused datetime patching for these specific assertions.)

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/unit/test_agent.py -k market -v`
Expected: FAIL — `mgmt.agent.agent` has no `get_market_status_sync` to patch.

- [ ] **Step 3: Update `mgmt/agent/agent.py`**

Add the import near the top of the module:

```python
from extraction.market_status import get_market_status_sync
```

Replace the `_is_market_open` body (lines 131-136) with:

```python
def _is_market_open() -> bool:
    return get_market_status_sync()["status"] == "Open"
```

- [ ] **Step 4: Update `chat/prompt.py`**

Add the import near the top of the module:

```python
from extraction.market_status import get_market_status_sync
```

Replace the `_is_market_open` body (lines 64-69) with:

```python
def _is_market_open() -> bool:
    return get_market_status_sync()["status"] == "Open"
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/unit/test_agent.py tests/unit/test_chat_bengali.py -v`
Expected: PASS. (`test_chat_bengali.py` builds the system message; with no Redis it falls back to clock and still returns a string — no assertion on Open/Closed there.)

- [ ] **Step 6: Commit**

```bash
git add chat/prompt.py mgmt/agent/agent.py tests/unit/test_agent.py
git commit -m "feat(market): LLM prompt builders read real market status"
```

---

## Task 11: Frontend — `status_source` type + `(est.)` marker

**Files:**
- Modify: `frontend/lib/types.ts`, `frontend/app/(app)/dashboard/page.tsx`, `frontend/components/stocks/LivePrice.tsx`

- [ ] **Step 1: Add the type field (`frontend/lib/types.ts`)**

In `interface MarketIndices` (after `market_status: string`, line 9) add:

```typescript
  status_source?: string
```

In the `LivePrice` interface (after `market_status: string`, line 112) add:

```typescript
  status_source?: string
```

- [ ] **Step 2: Dashboard badge (`frontend/app/(app)/dashboard/page.tsx`)**

Replace the badge content line (line 28) `{idx.market_status}` with:

```tsx
            {idx.market_status}
            {idx.status_source && idx.status_source !== 'dse_direct' ? ' (est.)' : ''}
```

- [ ] **Step 3: LivePrice marker (`frontend/components/stocks/LivePrice.tsx`)**

After the `isLive` const (line 88) add:

```tsx
  const estimated = !!data.status_source && data.status_source !== 'dse_direct'
```

Replace the status label expression (line 117) `{isLive ? \`live · as of ${asOf}\` : 'market closed'}` with:

```tsx
            {isLive ? `live · as of ${asOf}` : 'market closed'}
            {estimated ? ' (est.)' : ''}
```

Add `status_source` to the closed seed default (line 32) and the open seed (line 44):

```tsx
      market_status: 'Closed', status_source: undefined, as_of: new Date().toISOString(),
```

(open seed: leave `status_source` unset — the live fetch supplies it.)

- [ ] **Step 4: Lint + build**

Run: `cd frontend && npm run lint && npm run build`
Expected: no type errors; build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/types.ts "frontend/app/(app)/dashboard/page.tsx" frontend/components/stocks/LivePrice.tsx
git commit -m "feat(market): UI shows (est.) when status is a clock fallback"
```

---

## Task 12: Smoke test for the live adapter (project convention)

**Files:**
- Create: `tests/smoke/test_dse_market_status_smoke.py`

- [ ] **Step 1: Write the smoke test**

```python
# tests/smoke/test_dse_market_status_smoke.py
"""Live smoke test — hits dsebd.org. Network required; not part of `make test`."""
import pytest

from extraction.adapters.dse_direct.market_status import DSEMarketStatusAdapter


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_dse_market_status_live():
    result = await DSEMarketStatusAdapter().fetch()
    rec = result.data.to_dict("records")[0]
    assert rec["status"] in ("Open", "Closed")
    assert "Market Status" in rec["raw_label"]
```

- [ ] **Step 2: Run it (network required)**

Run: `pytest tests/smoke/test_dse_market_status_smoke.py -v`
Expected: PASS — prints a real Open/Closed value. If the DSE markup has shifted, this fails first (canary for the parser).

- [ ] **Step 3: Commit**

```bash
git add tests/smoke/test_dse_market_status_smoke.py
git commit -m "test(market): live smoke test for DSE status adapter"
```

---

## Task 13: Full suite + integration check

- [ ] **Step 1: Run the unit suite**

Run: `make test`
Expected: PASS (no references to removed `_market_status` / `_market_is_open`).

- [ ] **Step 2: Lint + typecheck**

Run: `make check`
Expected: ruff clean, mypy strict clean. (Watch for: unused imports of `datetime`/`DHAKA_TZ` in `test_api_market_indices.py`; unused `pytz`/`BD_TZ` left in `scheduler.py` after deleting `_market_is_open` — `BD_TZ` is still used elsewhere, keep it.)

- [ ] **Step 3: Manual end-to-end smoke (optional, requires stack up)**

```bash
make up
# trigger one refresh:
python -c "import asyncio; from extraction.market_status import refresh_market_status; print(asyncio.run(refresh_market_status()))"
curl -s localhost:8001/market/indices | python -m json.tool | grep -E "market_status|status_source"
```
Expected: refresh prints a record with `source` `dse_direct`; the endpoint shows the same status + `status_source`.

- [ ] **Step 4: Commit any cleanup**

```bash
git add -A
git commit -m "chore(market): suite + lint cleanup for market-status feature"
```

---

## Self-Review Notes (for the implementer)

- **Spec coverage:** adapter (T2) ✓, table+Redis (T4/T5) ✓, store readers + fallback (T5) ✓, morning poll-until-open + immediate live trigger (T7) ✓, afternoon poll-until-closed (T7) ✓, gate swap (T7) ✓, all 5 clock sites (T8 market, T9 stocks, T10 prompt+agent, T7 scheduler) ✓, `status_source` API+UI (T8/T9/T11) ✓, config (T6) ✓, clock fallback tag (T5) ✓.
- **Deviation from spec:** the adapter does **not** persist `last_update_label` (no column, unused) — dropped as YAGNI; `raw_label` is still captured.
- **Type consistency:** session record shape `{status, source, raw_label, session_date, checked_at}` is produced by `refresh_market_status`/`_write`; readers return `{status, source, checked_at}`. Redis key `cache:market:session` is identical in `_write`, `get_market_status`, `get_market_status_sync`. Day flags: `market:open_triggered:<date>`, `market:closed_confirmed:<date>`.
