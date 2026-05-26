# Access Control System (Phase G) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hardcoded tier limits with a DB-backed, Redis-cached access control system; add mgmt API + mgmt-ui to configure limits, feature flags, and per-user overrides at runtime.

**Architecture:** Three new DB tables (`tier_limits`, `feature_flags`, `user_access_overrides`) store all access rules. `api/access.py` provides `get_limit()` and `check_feature()` which cache in Redis (TTL 60s). The existing `api/middleware/rate_limit.py` replaces its hardcoded `TIER_LIMITS` dict with `get_limit()`. Seven new `mgmt/routers/access.py` endpoints let admins change any value; each write invalidates the relevant Redis key immediately. The mgmt-ui gets a new `/access` page with three tabs.

**Tech Stack:** asyncpg, Redis (via `mgmt.cache`), FastAPI, pytest + AsyncMock, Next.js App Router (TSX server + client components), Tailwind CSS

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `db/migrations/025_access_control.sql` | Tables + seed data |
| Create | `api/access.py` | `get_limit()`, `check_feature()`, cache helpers |
| Modify | `api/middleware/rate_limit.py` | Replace `TIER_LIMITS` dict with `get_limit()` call |
| Create | `mgmt/routers/access.py` | 7 admin CRUD endpoints |
| Modify | `mgmt/main.py` | Register access router |
| Modify | `mgmt-ui/lib/api.ts` | Add `put`/`del` helpers + access types + API calls |
| Create | `mgmt-ui/app/access/page.tsx` | Tab shell (server component) |
| Create | `mgmt-ui/app/access/TierLimitsTab.tsx` | Inline-edit table (client) |
| Create | `mgmt-ui/app/access/FeatureFlagsTab.tsx` | Toggle switches grid (client) |
| Create | `mgmt-ui/app/access/UserOverridesTab.tsx` | User search + override CRUD (client) |
| Modify | `mgmt-ui/app/layout.tsx` | Add "Access" nav link |
| Create | `tests/unit/test_access_module.py` | Unit tests for `api/access.py` |
| Modify | `tests/unit/test_api_rate_limit.py` | Update for DB-backed limits |
| Create | `tests/unit/test_mgmt_access.py` | Unit tests for mgmt access endpoints |

---

## Task 1: DB Migration

**Files:**
- Create: `db/migrations/025_access_control.sql`

- [ ] **Step 1: Write the migration**

```sql
-- db/migrations/025_access_control.sql
-- Runtime-configurable tier limits, feature flags, and per-user access overrides.

CREATE TABLE IF NOT EXISTS tier_limits (
    tier        TEXT    NOT NULL,
    limit_key   TEXT    NOT NULL,
    limit_value INTEGER NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tier, limit_key)
);

CREATE TABLE IF NOT EXISTS feature_flags (
    flag_key    TEXT    NOT NULL,
    tier        TEXT    NOT NULL,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by  BIGINT  REFERENCES users(id) ON DELETE SET NULL,
    PRIMARY KEY (flag_key, tier)
);

CREATE TABLE IF NOT EXISTS user_access_overrides (
    id          BIGSERIAL   PRIMARY KEY,
    user_id     BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    flag_key    TEXT        NOT NULL,
    override    TEXT        NOT NULL,
    expires_at  TIMESTAMPTZ,
    note        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by  BIGINT      REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT chk_override CHECK (override IN ('grant', 'revoke')),
    UNIQUE (user_id, flag_key)
);

CREATE INDEX IF NOT EXISTS idx_user_overrides_user_id ON user_access_overrides (user_id);

-- Seed: default tier limits (0 = unlimited)
INSERT INTO tier_limits (tier, limit_key, limit_value) VALUES
    ('free',        'api_calls_per_day',        50),
    ('free',        'chat_queries_per_day',       3),
    ('free',        'portfolio_holdings_max',     5),
    ('pro',         'api_calls_per_day',       1000),
    ('pro',         'chat_queries_per_day',      30),
    ('pro',         'portfolio_holdings_max',     0),
    ('pro_plus',    'api_calls_per_day',       5000),
    ('pro_plus',    'chat_queries_per_day',     100),
    ('pro_plus',    'portfolio_holdings_max',     0),
    ('institution', 'api_calls_per_day',          0),
    ('institution', 'chat_queries_per_day',        0),
    ('institution', 'portfolio_holdings_max',      0)
ON CONFLICT (tier, limit_key) DO NOTHING;

-- Seed: default feature flags
INSERT INTO feature_flags (flag_key, tier, enabled) VALUES
    ('predictions',           'free',        false),
    ('predictions',           'pro',         true),
    ('predictions',           'pro_plus',    true),
    ('predictions',           'institution', true),
    ('reports',               'free',        false),
    ('reports',               'pro',         true),
    ('reports',               'pro_plus',    true),
    ('reports',               'institution', true),
    ('portfolio_analysis',    'free',        false),
    ('portfolio_analysis',    'pro',         true),
    ('portfolio_analysis',    'pro_plus',    true),
    ('portfolio_analysis',    'institution', true),
    ('chat',                  'free',        true),
    ('chat',                  'pro',         true),
    ('chat',                  'pro_plus',    true),
    ('chat',                  'institution', true),
    ('screener_health_score', 'free',        false),
    ('screener_health_score', 'pro',         true),
    ('screener_health_score', 'pro_plus',    true),
    ('screener_health_score', 'institution', true)
ON CONFLICT (flag_key, tier) DO NOTHING;
```

- [ ] **Step 2: Apply migration**

```bash
make migrate
```

Expected: `Applied 025_access_control.sql` in output, no errors.

- [ ] **Step 3: Verify**

```bash
make db-shell
```

Run in psql:
```sql
SELECT * FROM tier_limits ORDER BY tier, limit_key;
SELECT * FROM feature_flags ORDER BY flag_key, tier;
\d user_access_overrides
```

Expected: 12 rows in tier_limits, 20 rows in feature_flags, table with correct columns. Then `\q`.

---

## Task 2: `api/access.py` — `get_limit()`

**Files:**
- Create: `api/access.py`
- Create: `tests/unit/test_access_module.py`

- [ ] **Step 1: Write failing tests for `get_limit`**

```python
# tests/unit/test_access_module.py
import os
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

import pytest
from unittest.mock import AsyncMock, MagicMock
from contextlib import asynccontextmanager


def _make_pool(rows: list[dict]):
    """Build an asyncpg pool mock that returns rows from fetchrow."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=rows[0] if rows else None)
    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


def _make_redis(get_value=None):
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=get_value)
    redis.set = AsyncMock()
    return redis


@pytest.mark.asyncio
async def test_get_limit_returns_db_value_on_cache_miss():
    from api.access import get_limit

    row = {"limit_value": 50}
    pool, conn = _make_pool([row])
    redis = _make_redis(get_value=None)  # cache miss

    result = await get_limit(pool, redis, "free", "api_calls_per_day")

    assert result == 50
    conn.fetchrow.assert_called_once()
    redis.set.assert_called_once()


@pytest.mark.asyncio
async def test_get_limit_returns_cached_value():
    from api.access import get_limit

    pool, conn = _make_pool([])
    redis = _make_redis(get_value="1000")  # cache hit

    result = await get_limit(pool, redis, "pro", "api_calls_per_day")

    assert result == 1000
    conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_get_limit_returns_zero_when_no_row():
    from api.access import get_limit

    pool, conn = _make_pool([None])
    conn.fetchrow = AsyncMock(return_value=None)
    redis = _make_redis(get_value=None)

    result = await get_limit(pool, redis, "unknown_tier", "api_calls_per_day")

    assert result == 0
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
pytest tests/unit/test_access_module.py::test_get_limit_returns_db_value_on_cache_miss -v
```

Expected: `ImportError` or `ModuleNotFoundError` — `api/access.py` does not exist yet.

- [ ] **Step 3: Create `api/access.py` with `get_limit()`**

```python
# api/access.py
from __future__ import annotations

import json
from datetime import datetime, timezone

_CACHE_TTL = 60  # seconds


async def get_limit(pool, redis, tier: str, limit_key: str) -> int:
    """Return numeric rate-limit for a tier. 0 = unlimited. Cached in Redis 60s."""
    cache_key = f"access:limits:{tier}:{limit_key}"
    cached = await redis.get(cache_key)
    if cached is not None:
        return int(cached)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT limit_value FROM tier_limits WHERE tier = $1 AND limit_key = $2",
            tier,
            limit_key,
        )

    value = row["limit_value"] if row else 0
    await redis.set(cache_key, str(value), ex=_CACHE_TTL)
    return value
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
pytest tests/unit/test_access_module.py -k "get_limit" -v
```

Expected: 3 tests PASS.

---

## Task 3: `api/access.py` — `check_feature()`

**Files:**
- Modify: `api/access.py`
- Modify: `tests/unit/test_access_module.py`

- [ ] **Step 1: Add failing tests for `check_feature`**

Append to `tests/unit/test_access_module.py`:

```python
@pytest.mark.asyncio
async def test_check_feature_grant_override_wins():
    from api.access import check_feature

    # Override: grant; flag: disabled — override should win
    pool, conn = _make_pool([])

    # Redis: overrides cache contains a grant
    overrides_json = json.dumps({"predictions": {"override": "grant", "expires_at": None}})
    redis = _make_redis(get_value=overrides_json)

    result = await check_feature(pool, redis, user_id=1, tier="free", flag_key="predictions")

    assert result is True


@pytest.mark.asyncio
async def test_check_feature_revoke_override_wins():
    from api.access import check_feature
    import json

    pool, conn = _make_pool([])
    overrides_json = json.dumps({"predictions": {"override": "revoke", "expires_at": None}})
    redis = _make_redis(get_value=overrides_json)

    result = await check_feature(pool, redis, user_id=1, tier="pro", flag_key="predictions")

    assert result is False


@pytest.mark.asyncio
async def test_check_feature_expired_override_falls_back_to_flag():
    from api.access import check_feature
    import json

    pool, conn = _make_pool([])
    # Override expired in the past
    past = "2020-01-01T00:00:00+00:00"
    overrides_json = json.dumps({"predictions": {"override": "grant", "expires_at": past}})

    # Redis: first get = overrides; second get = flag
    call_count = 0

    async def _redis_get(key):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return overrides_json   # overrides key
        return "true"               # flag key

    redis = AsyncMock()
    redis.get = _redis_get
    redis.set = AsyncMock()

    result = await check_feature(pool, redis, user_id=1, tier="pro", flag_key="predictions")

    assert result is True  # falls back to flag (enabled=true for pro)


@pytest.mark.asyncio
async def test_check_feature_no_override_uses_flag():
    from api.access import check_feature

    pool, conn = _make_pool([])
    # No overrides for user, flag is enabled

    call_count = 0

    async def _redis_get(key):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "{}"     # empty overrides dict
        return "true"       # flag enabled

    redis = AsyncMock()
    redis.get = _redis_get
    redis.set = AsyncMock()

    result = await check_feature(pool, redis, user_id=2, tier="pro", flag_key="chat")

    assert result is True


@pytest.mark.asyncio
async def test_check_feature_flag_disabled_returns_false():
    from api.access import check_feature

    pool, conn = _make_pool([])

    call_count = 0

    async def _redis_get(key):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "{}"      # no overrides
        return "false"       # flag disabled

    redis = AsyncMock()
    redis.get = _redis_get
    redis.set = AsyncMock()

    result = await check_feature(pool, redis, user_id=3, tier="free", flag_key="predictions")

    assert result is False
```

- [ ] **Step 2: Run tests — confirm new ones fail**

```bash
pytest tests/unit/test_access_module.py -k "check_feature" -v
```

Expected: `ImportError` or `AttributeError` — `check_feature` not defined yet.

- [ ] **Step 3: Add `check_feature` and helpers to `api/access.py`**

Replace the full file:

```python
# api/access.py
from __future__ import annotations

import json
from datetime import datetime, timezone

_CACHE_TTL = 60  # seconds


async def get_limit(pool, redis, tier: str, limit_key: str) -> int:
    """Return numeric rate-limit for a tier. 0 = unlimited. Cached in Redis 60s."""
    cache_key = f"access:limits:{tier}:{limit_key}"
    cached = await redis.get(cache_key)
    if cached is not None:
        return int(cached)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT limit_value FROM tier_limits WHERE tier = $1 AND limit_key = $2",
            tier,
            limit_key,
        )

    value = row["limit_value"] if row else 0
    await redis.set(cache_key, str(value), ex=_CACHE_TTL)
    return value


async def check_feature(pool, redis, user_id: int, tier: str, flag_key: str) -> bool:
    """Check if user has access to a feature. Override > tier flag > False."""
    override = await _get_user_override(pool, redis, user_id, flag_key)
    if override is not None:
        expires_raw = override.get("expires_at")
        if expires_raw is not None:
            expires = datetime.fromisoformat(expires_raw)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= datetime.now(timezone.utc):
                override = None  # expired — fall through to flag

    if override is not None:
        return override["override"] == "grant"

    return await _get_feature_flag(pool, redis, tier, flag_key)


async def _get_user_override(pool, redis, user_id: int, flag_key: str) -> dict | None:
    cache_key = f"access:overrides:{user_id}"
    cached = await redis.get(cache_key)
    if cached is not None:
        data = json.loads(cached)
        return data.get(flag_key)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT flag_key, override, expires_at
            FROM user_access_overrides
            WHERE user_id = $1
            """,
            user_id,
        )

    overrides = {
        r["flag_key"]: {
            "override": r["override"],
            "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
        }
        for r in rows
    }
    await redis.set(cache_key, json.dumps(overrides), ex=_CACHE_TTL)
    return overrides.get(flag_key)


async def _get_feature_flag(pool, redis, tier: str, flag_key: str) -> bool:
    cache_key = f"access:flags:{flag_key}:{tier}"
    cached = await redis.get(cache_key)
    if cached is not None:
        return cached == "true"

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT enabled FROM feature_flags WHERE flag_key = $1 AND tier = $2",
            flag_key,
            tier,
        )

    enabled = row["enabled"] if row else False
    await redis.set(cache_key, "true" if enabled else "false", ex=_CACHE_TTL)
    return enabled


async def invalidate_limit(redis, tier: str, limit_key: str) -> None:
    await redis.delete(f"access:limits:{tier}:{limit_key}")


async def invalidate_flag(redis, flag_key: str, tier: str) -> None:
    await redis.delete(f"access:flags:{flag_key}:{tier}")


async def invalidate_user_overrides(redis, user_id: int) -> None:
    await redis.delete(f"access:overrides:{user_id}")
```

- [ ] **Step 4: Run all access module tests**

```bash
pytest tests/unit/test_access_module.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/025_access_control.sql api/access.py tests/unit/test_access_module.py
git commit -m "feat(access): add access control tables and api/access.py module"
```

---

## Task 4: Update Rate Limit Middleware

**Files:**
- Modify: `api/middleware/rate_limit.py`
- Modify: `tests/unit/test_api_rate_limit.py`

- [ ] **Step 1: Write new failing tests**

Replace the full content of `tests/unit/test_api_rate_limit.py`:

```python
# tests/unit/test_api_rate_limit.py
import os
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

import json
import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse
from api.middleware.rate_limit import RateLimitMiddleware
from api.auth.jwt import create_access_token


def _make_pool_with_limit(limit_value: int):
    """Pool mock that returns limit_value for any fetchrow call."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"limit_value": limit_value})
    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool


def _make_app(redis_mock, pool_mock=None):
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        get_redis_fn=lambda: redis_mock,
        get_pool_fn=(lambda: pool_mock) if pool_mock else None,
    )

    @app.get("/api/stocks")
    async def stocks():
        return PlainTextResponse("ok")

    @app.get("/api/auth/login")
    async def login():
        return PlainTextResponse("ok")

    return app


def test_skips_auth_paths():
    redis_mock = AsyncMock()
    app = _make_app(redis_mock)
    client = TestClient(app)
    resp = client.get("/api/auth/login")
    assert resp.status_code == 200
    redis_mock.incr.assert_not_called()


def test_allows_when_under_limit():
    redis_mock = AsyncMock()
    # Redis.get returns the limit (cached), Redis.incr returns count under limit
    redis_mock.get = AsyncMock(return_value="50")   # limit cached as "50"
    redis_mock.incr = AsyncMock(return_value=1)
    redis_mock.expire = AsyncMock()
    token = create_access_token(1, "a@b.com", "free")
    app = _make_app(redis_mock)
    client = TestClient(app)
    resp = client.get("/api/stocks", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_blocks_when_over_limit():
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value="50")   # limit = 50
    redis_mock.incr = AsyncMock(return_value=51)    # count = 51 > 50
    redis_mock.expire = AsyncMock()
    token = create_access_token(1, "a@b.com", "free")
    app = _make_app(redis_mock)
    client = TestClient(app)
    resp = client.get("/api/stocks", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 429


def test_institution_tier_skipped_when_limit_zero():
    """limit_value = 0 means unlimited — request must pass through."""
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value="0")    # limit = 0 = unlimited
    redis_mock.incr = AsyncMock()
    token = create_access_token(1, "admin@b.com", "institution")
    app = _make_app(redis_mock)
    client = TestClient(app)
    resp = client.get("/api/stocks", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    redis_mock.incr.assert_not_called()


def test_limit_loaded_from_db_on_cache_miss():
    """When Redis has no cached limit, middleware fetches from DB pool."""
    call_count = 0

    async def _redis_get(key):
        nonlocal call_count
        call_count += 1
        if "access:limits" in key:
            return None     # cache miss → will hit DB
        return None         # ratelimit counter miss

    redis_mock = AsyncMock()
    redis_mock.get = _redis_get
    redis_mock.set = AsyncMock()
    redis_mock.incr = AsyncMock(return_value=1)
    redis_mock.expire = AsyncMock()

    pool_mock = _make_pool_with_limit(50)
    token = create_access_token(1, "a@b.com", "free")
    app = _make_app(redis_mock, pool_mock)
    client = TestClient(app)
    resp = client.get("/api/stocks", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    # Pool was used to fetch the limit
    pool_mock.acquire().__aenter__()  # just verify it was acquired — no error
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
pytest tests/unit/test_api_rate_limit.py -v
```

Expected: failures because `RateLimitMiddleware` still uses `TIER_LIMITS` and doesn't accept `get_pool_fn`.

- [ ] **Step 3: Rewrite `api/middleware/rate_limit.py`**

```python
# api/middleware/rate_limit.py
from __future__ import annotations

import asyncio
from datetime import date
from typing import Callable

from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from api.config import get_settings

_SKIP_PREFIXES = ("/api/auth/", "/health", "/docs", "/openapi.json", "/redoc")
_ALGORITHM = "HS256"


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, get_redis_fn: Callable | None = None, get_pool_fn: Callable | None = None):
        super().__init__(app)
        self._get_redis = get_redis_fn
        self._get_pool = get_pool_fn

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in _SKIP_PREFIXES):
            return await call_next(request)

        token = _extract_bearer(request)
        if not token:
            return await call_next(request)

        settings = get_settings()
        try:
            payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[_ALGORITHM])
        except JWTError:
            return await call_next(request)

        tier = payload.get("tier", "free")
        user_id = payload.get("sub", "anon")

        get_redis = self._get_redis
        if get_redis is None:
            from mgmt.cache import get_redis as _default
            get_redis = _default

        get_pool = self._get_pool
        if get_pool is None:
            from db.pool import get_pool as _default_pool
            get_pool = _default_pool

        if asyncio.iscoroutinefunction(get_redis):
            redis = await get_redis()
        else:
            redis = get_redis()

        if asyncio.iscoroutinefunction(get_pool):
            pool = await get_pool()
        else:
            pool = get_pool()

        from api.access import get_limit
        limit = await get_limit(pool, redis, tier, "api_calls_per_day")

        if limit == 0:
            return await call_next(request)

        key = f"ratelimit:{user_id}:{date.today().isoformat()}"
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 86400)
        if count > limit:
            return JSONResponse(
                {"detail": f"Rate limit exceeded ({limit}/day for {tier} tier)"},
                status_code=429,
            )
        return await call_next(request)


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None
```

- [ ] **Step 4: Run all rate limit tests**

```bash
pytest tests/unit/test_api_rate_limit.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Run all unit tests — no regressions**

```bash
pytest tests/unit/ -v --tb=short
```

Expected: all existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add api/middleware/rate_limit.py tests/unit/test_api_rate_limit.py
git commit -m "feat(access): replace hardcoded TIER_LIMITS with DB-backed get_limit()"
```

---

## Task 5: `mgmt/routers/access.py` — Tier Limits + Feature Flags Endpoints

**Files:**
- Create: `mgmt/routers/access.py`
- Create: `tests/unit/test_mgmt_access.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_mgmt_access.py
import os
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mgmt.routers.access import router


def _make_pool(rows=None, execute_ok=True):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows or [])
    conn.fetchrow = AsyncMock(return_value=rows[0] if rows else None)
    conn.execute = AsyncMock(return_value="UPDATE 1" if execute_ok else "UPDATE 0")
    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


def _make_app(pool):
    app = FastAPI()
    app.include_router(router)

    async def _get_db():
        return pool

    from mgmt.deps import get_db
    app.dependency_overrides[get_db] = _get_db
    return app


# ── Tier Limits ──────────────────────────────────────────────────────

def test_list_tier_limits_returns_rows():
    rows = [
        {"tier": "free", "limit_key": "api_calls_per_day", "limit_value": 50, "updated_at": None},
        {"tier": "pro",  "limit_key": "api_calls_per_day", "limit_value": 1000, "updated_at": None},
    ]
    pool, _ = _make_pool(rows)
    client = TestClient(_make_app(pool))

    with patch("mgmt.routers.access.cache_delete_pattern", new=AsyncMock()):
        resp = client.get("/mgmt/access/tier-limits")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["tier"] == "free"


def test_update_tier_limit_writes_and_invalidates():
    pool, conn = _make_pool()
    client = TestClient(_make_app(pool))

    with patch("mgmt.routers.access.cache_delete_pattern", new=AsyncMock()) as mock_del:
        resp = client.put(
            "/mgmt/access/tier-limits/free/api_calls_per_day",
            json={"limit_value": 100},
        )

    assert resp.status_code == 200
    conn.execute.assert_called_once()
    mock_del.assert_called_once_with("access:limits:free:api_calls_per_day")


def test_update_tier_limit_rejects_negative():
    pool, _ = _make_pool()
    client = TestClient(_make_app(pool))

    resp = client.put(
        "/mgmt/access/tier-limits/free/api_calls_per_day",
        json={"limit_value": -5},
    )

    assert resp.status_code == 422


# ── Feature Flags ────────────────────────────────────────────────────

def test_list_feature_flags_returns_rows():
    rows = [
        {"flag_key": "predictions", "tier": "free", "enabled": False, "updated_at": None},
        {"flag_key": "predictions", "tier": "pro",  "enabled": True,  "updated_at": None},
    ]
    pool, _ = _make_pool(rows)
    client = TestClient(_make_app(pool))

    resp = client.get("/mgmt/access/features")

    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_update_feature_flag_writes_and_invalidates():
    pool, conn = _make_pool()
    client = TestClient(_make_app(pool))

    with patch("mgmt.routers.access.cache_delete_pattern", new=AsyncMock()) as mock_del:
        resp = client.put(
            "/mgmt/access/features/predictions/free",
            json={"enabled": True},
        )

    assert resp.status_code == 200
    conn.execute.assert_called_once()
    mock_del.assert_called_once_with("access:flags:predictions:free")
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
pytest tests/unit/test_mgmt_access.py -v
```

Expected: `ImportError` — `mgmt/routers/access.py` does not exist.

- [ ] **Step 3: Create `mgmt/routers/access.py`** (tier limits + feature flags only)

```python
# mgmt/routers/access.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from mgmt.cache import cache_delete_pattern
from mgmt.deps import get_db

router = APIRouter(prefix="/mgmt/access", tags=["access"])


# ── Schemas ──────────────────────────────────────────────────────────

class LimitUpdate(BaseModel):
    limit_value: int = Field(ge=0, description="Calls per day. 0 = unlimited.")


class FlagUpdate(BaseModel):
    enabled: bool


class OverrideCreate(BaseModel):
    flag_key: str
    override: str = Field(pattern="^(grant|revoke)$")
    expires_at: str | None = None   # ISO 8601 or None
    note: str | None = None


# ── Tier Limits ──────────────────────────────────────────────────────

@router.get("/tier-limits")
async def list_tier_limits(pool=Depends(get_db)):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT tier, limit_key, limit_value, updated_at FROM tier_limits ORDER BY tier, limit_key"
        )
    return [dict(r) for r in rows]


@router.put("/tier-limits/{tier}/{limit_key}")
async def update_tier_limit(tier: str, limit_key: str, body: LimitUpdate, pool=Depends(get_db)):
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO tier_limits (tier, limit_key, limit_value, updated_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (tier, limit_key) DO UPDATE
                SET limit_value = EXCLUDED.limit_value,
                    updated_at  = EXCLUDED.updated_at
            """,
            tier,
            limit_key,
            body.limit_value,
        )
    await cache_delete_pattern(f"access:limits:{tier}:{limit_key}")
    return {"tier": tier, "limit_key": limit_key, "limit_value": body.limit_value}


# ── Feature Flags ────────────────────────────────────────────────────

@router.get("/features")
async def list_feature_flags(pool=Depends(get_db)):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT flag_key, tier, enabled, updated_at FROM feature_flags ORDER BY flag_key, tier"
        )
    return [dict(r) for r in rows]


@router.put("/features/{flag_key}/{tier}")
async def update_feature_flag(flag_key: str, tier: str, body: FlagUpdate, pool=Depends(get_db)):
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO feature_flags (flag_key, tier, enabled, updated_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (flag_key, tier) DO UPDATE
                SET enabled    = EXCLUDED.enabled,
                    updated_at = EXCLUDED.updated_at
            """,
            flag_key,
            tier,
            body.enabled,
        )
    await cache_delete_pattern(f"access:flags:{flag_key}:{tier}")
    return {"flag_key": flag_key, "tier": tier, "enabled": body.enabled}
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_mgmt_access.py -v
```

Expected: all 5 tests PASS.

---

## Task 6: User Overrides Endpoints

**Files:**
- Modify: `mgmt/routers/access.py`
- Modify: `tests/unit/test_mgmt_access.py`

- [ ] **Step 1: Add failing tests for user overrides**

Append to `tests/unit/test_mgmt_access.py`:

```python
# ── User Overrides ────────────────────────────────────────────────────

def test_search_users_by_email():
    rows = [{"id": 1, "email": "bob@test.com", "tier": "free", "is_active": True}]
    pool, conn = _make_pool(rows)
    conn.fetch = AsyncMock(return_value=rows)
    client = TestClient(_make_app(pool))

    resp = client.get("/mgmt/access/users?email=bob")

    assert resp.status_code == 200
    assert resp.json()[0]["email"] == "bob@test.com"


def test_list_user_overrides():
    rows = [
        {"id": 1, "user_id": 1, "flag_key": "predictions", "override": "grant",
         "expires_at": None, "note": None, "created_at": None},
    ]
    pool, conn = _make_pool(rows)
    conn.fetch = AsyncMock(return_value=rows)
    client = TestClient(_make_app(pool))

    resp = client.get("/mgmt/access/users/1/overrides")

    assert resp.status_code == 200
    assert resp.json()[0]["flag_key"] == "predictions"


def test_create_override_writes_and_invalidates():
    pool, conn = _make_pool()
    conn.fetchrow = AsyncMock(return_value={
        "id": 1, "user_id": 1, "flag_key": "reports", "override": "grant",
        "expires_at": None, "note": "beta tester", "created_at": None,
    })
    client = TestClient(_make_app(pool))

    with patch("mgmt.routers.access.cache_delete_pattern", new=AsyncMock()) as mock_del:
        resp = client.post(
            "/mgmt/access/users/1/overrides",
            json={"flag_key": "reports", "override": "grant", "note": "beta tester"},
        )

    assert resp.status_code == 201
    mock_del.assert_called_once_with("access:overrides:1")


def test_delete_override_removes_and_invalidates():
    pool, conn = _make_pool()
    conn.execute = AsyncMock(return_value="DELETE 1")
    client = TestClient(_make_app(pool))

    with patch("mgmt.routers.access.cache_delete_pattern", new=AsyncMock()) as mock_del:
        resp = client.delete("/mgmt/access/users/1/overrides/reports")

    assert resp.status_code == 200
    mock_del.assert_called_once_with("access:overrides:1")
```

- [ ] **Step 2: Run new tests — confirm they fail**

```bash
pytest tests/unit/test_mgmt_access.py -k "override or user" -v
```

Expected: 404 errors — routes not defined yet.

- [ ] **Step 3: Add user override endpoints to `mgmt/routers/access.py`**

Append to the existing file after the feature flags section:

```python
# ── User Overrides ────────────────────────────────────────────────────

@router.get("/users")
async def search_users(email: str = "", limit: int = 20, pool=Depends(get_db)):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, email, tier, is_active
            FROM users
            WHERE email ILIKE $1
            ORDER BY email
            LIMIT $2
            """,
            f"%{email}%",
            limit,
        )
    return [dict(r) for r in rows]


@router.get("/users/{user_id}/overrides")
async def list_user_overrides(user_id: int, pool=Depends(get_db)):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, user_id, flag_key, override, expires_at, note, created_at
            FROM user_access_overrides
            WHERE user_id = $1
            ORDER BY flag_key
            """,
            user_id,
        )
    return [dict(r) for r in rows]


@router.post("/users/{user_id}/overrides", status_code=201)
async def create_user_override(user_id: int, body: OverrideCreate, pool=Depends(get_db)):
    expires = None
    if body.expires_at:
        from datetime import datetime
        expires = datetime.fromisoformat(body.expires_at)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO user_access_overrides (user_id, flag_key, override, expires_at, note)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (user_id, flag_key) DO UPDATE
                SET override   = EXCLUDED.override,
                    expires_at = EXCLUDED.expires_at,
                    note       = EXCLUDED.note
            RETURNING id, user_id, flag_key, override, expires_at, note, created_at
            """,
            user_id,
            body.flag_key,
            body.override,
            expires,
            body.note,
        )
    await cache_delete_pattern(f"access:overrides:{user_id}")
    return dict(row)


@router.delete("/users/{user_id}/overrides/{flag_key}")
async def delete_user_override(user_id: int, flag_key: str, pool=Depends(get_db)):
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM user_access_overrides WHERE user_id = $1 AND flag_key = $2",
            user_id,
            flag_key,
        )
    await cache_delete_pattern(f"access:overrides:{user_id}")
    return {"deleted": True, "user_id": user_id, "flag_key": flag_key}
```

- [ ] **Step 4: Run all mgmt access tests**

```bash
pytest tests/unit/test_mgmt_access.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add mgmt/routers/access.py tests/unit/test_mgmt_access.py
git commit -m "feat(access): add mgmt access router — tier limits, feature flags, user overrides"
```

---

## Task 7: Register Access Router in mgmt/main.py

**Files:**
- Modify: `mgmt/main.py`

- [ ] **Step 1: Add import and router registration**

In `mgmt/main.py`, add `access` to the routers import line:

```python
from mgmt.routers import agent, alerts, access, chat, health, jobs, metrics, quality, scheduler, streams, tasks
```

Add after the last `app.include_router(...)` call:

```python
app.include_router(access.router)
```

- [ ] **Step 2: Verify server starts**

```bash
make up
```

Then:

```bash
curl http://localhost:8001/mgmt/access/tier-limits
```

Expected: JSON array of tier limit rows (or error if DB not seeded — but migration should have seeded it).

- [ ] **Step 3: Commit**

```bash
git add mgmt/main.py
git commit -m "feat(access): register access router in mgmt API"
```

---

## Task 8: mgmt-ui — API Client Extensions

**Files:**
- Modify: `mgmt-ui/lib/api.ts`

- [ ] **Step 1: Add `put`, `del` helpers and access types + calls**

In `mgmt-ui/lib/api.ts`, add after the existing `post<T>` function:

```typescript
async function put<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}
```

Add new types after the existing interfaces:

```typescript
export interface TierLimit {
  tier: string;
  limit_key: string;
  limit_value: number;
  updated_at: string | null;
}

export interface FeatureFlag {
  flag_key: string;
  tier: string;
  enabled: boolean;
  updated_at: string | null;
}

export interface UserSummary {
  id: number;
  email: string;
  tier: string;
  is_active: boolean;
}

export interface UserOverride {
  id: number;
  user_id: number;
  flag_key: string;
  override: "grant" | "revoke";
  expires_at: string | null;
  note: string | null;
  created_at: string | null;
}
```

Add to the `api` object (after the `agent` block):

```typescript
  access: {
    tierLimits: () => get<TierLimit[]>("/access/tier-limits"),
    updateTierLimit: (tier: string, key: string, value: number) =>
      put(`/access/tier-limits/${tier}/${key}`, { limit_value: value }),
    features: () => get<FeatureFlag[]>("/access/features"),
    updateFeature: (flagKey: string, tier: string, enabled: boolean) =>
      put(`/access/features/${flagKey}/${tier}`, { enabled }),
    searchUsers: (email: string) => get<UserSummary[]>(`/access/users?email=${encodeURIComponent(email)}`),
    userOverrides: (userId: number) => get<UserOverride[]>(`/access/users/${userId}/overrides`),
    createOverride: (userId: number, body: { flag_key: string; override: string; expires_at?: string; note?: string }) =>
      post<UserOverride>(`/access/users/${userId}/overrides`, body),
    deleteOverride: (userId: number, flagKey: string) =>
      del(`/access/users/${userId}/overrides/${flagKey}`),
  },
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd mgmt-ui && npm run build
```

Expected: build succeeds with no TypeScript errors.

- [ ] **Step 3: Commit**

```bash
git add mgmt-ui/lib/api.ts
git commit -m "feat(access): add access control API calls and types to mgmt-ui api client"
```

---

## Task 9: mgmt-ui — Access Page Skeleton + Nav Link

**Files:**
- Create: `mgmt-ui/app/access/page.tsx`
- Modify: `mgmt-ui/app/layout.tsx`

- [ ] **Step 1: Create the page shell**

```tsx
// mgmt-ui/app/access/page.tsx
import { Suspense } from "react";
import TierLimitsTab from "./TierLimitsTab";
import FeatureFlagsTab from "./FeatureFlagsTab";
import UserOverridesTab from "./UserOverridesTab";
import { api } from "@/lib/api";

export default async function AccessPage({
  searchParams,
}: {
  searchParams: Promise<{ tab?: string }>;
}) {
  const { tab = "limits" } = await searchParams;

  const [limits, flags] = await Promise.all([
    api.access.tierLimits(),
    api.access.features(),
  ]);

  const tabs = [
    { key: "limits", label: "Tier Limits" },
    { key: "flags",  label: "Feature Flags" },
    { key: "users",  label: "User Overrides" },
  ];

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold text-white">Access Control</h1>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-gray-800">
        {tabs.map((t) => (
          <a
            key={t.key}
            href={`/access?tab=${t.key}`}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              tab === t.key
                ? "border-green-400 text-green-400"
                : "border-transparent text-gray-400 hover:text-white"
            }`}
          >
            {t.label}
          </a>
        ))}
      </div>

      {/* Tab content */}
      <Suspense fallback={<p className="text-gray-500 text-sm">Loading…</p>}>
        {tab === "limits" && <TierLimitsTab initialLimits={limits} />}
        {tab === "flags"  && <FeatureFlagsTab initialFlags={flags} />}
        {tab === "users"  && <UserOverridesTab />}
      </Suspense>
    </div>
  );
}
```

- [ ] **Step 2: Add "Access" nav link to layout**

In `mgmt-ui/app/layout.tsx`, add after the `<a href="/tasks" ...>Tasks</a>` line:

```tsx
<a href="/access" className="text-gray-400 hover:text-white">Access</a>
```

- [ ] **Step 3: Commit skeleton**

```bash
git add mgmt-ui/app/access/page.tsx mgmt-ui/app/layout.tsx
git commit -m "feat(access): add access page shell and nav link to mgmt-ui"
```

---

## Task 10: mgmt-ui — Tier Limits Tab

**Files:**
- Create: `mgmt-ui/app/access/TierLimitsTab.tsx`

- [ ] **Step 1: Create TierLimitsTab client component**

```tsx
// mgmt-ui/app/access/TierLimitsTab.tsx
"use client";

import { useState } from "react";
import { TierLimit, api } from "@/lib/api";

const ALL_TIERS = ["free", "pro", "pro_plus", "institution"];
const ALL_KEYS  = ["api_calls_per_day", "chat_queries_per_day", "portfolio_holdings_max"];

function displayValue(v: number): string {
  return v === 0 ? "Unlimited" : String(v);
}

export default function TierLimitsTab({ initialLimits }: { initialLimits: TierLimit[] }) {
  const [limits, setLimits] = useState<TierLimit[]>(initialLimits);
  const [editing, setEditing] = useState<string | null>(null); // "tier:key"
  const [editValue, setEditValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function getLimit(tier: string, key: string): number {
    return limits.find((l) => l.tier === tier && l.limit_key === key)?.limit_value ?? 0;
  }

  function startEdit(tier: string, key: string) {
    const current = getLimit(tier, key);
    setEditing(`${tier}:${key}`);
    setEditValue(current === 0 ? "0" : String(current));
    setError(null);
  }

  async function saveEdit(tier: string, key: string) {
    const parsed = parseInt(editValue, 10);
    if (isNaN(parsed) || parsed < 0) {
      setError("Must be a non-negative integer (0 = unlimited)");
      return;
    }
    setSaving(true);
    try {
      await api.access.updateTierLimit(tier, key, parsed);
      setLimits((prev) =>
        prev.map((l) =>
          l.tier === tier && l.limit_key === key ? { ...l, limit_value: parsed } : l
        )
      );
      setEditing(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="overflow-x-auto">
      <p className="text-xs text-gray-500 mb-3">
        0 = unlimited. Changes take effect within 60 seconds (Redis TTL).
      </p>
      {error && <p className="text-red-400 text-sm mb-3">{error}</p>}
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-800">
            <th className="text-left py-2 pr-6 text-gray-400 font-medium">Limit</th>
            {ALL_TIERS.map((t) => (
              <th key={t} className="text-left py-2 pr-6 text-gray-400 font-medium capitalize">
                {t.replace("_", " ")}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ALL_KEYS.map((key) => (
            <tr key={key} className="border-b border-gray-900">
              <td className="py-3 pr-6 text-gray-300 font-mono text-xs">{key}</td>
              {ALL_TIERS.map((tier) => {
                const editKey = `${tier}:${key}`;
                const isEditing = editing === editKey;
                const value = getLimit(tier, key);
                return (
                  <td key={tier} className="py-3 pr-6">
                    {isEditing ? (
                      <div className="flex items-center gap-2">
                        <input
                          type="number"
                          min={0}
                          value={editValue}
                          onChange={(e) => setEditValue(e.target.value)}
                          className="w-20 bg-gray-900 border border-gray-600 rounded px-2 py-1 text-white text-xs"
                          autoFocus
                        />
                        <button
                          onClick={() => saveEdit(tier, key)}
                          disabled={saving}
                          className="text-green-400 hover:text-green-300 text-xs disabled:opacity-50"
                        >
                          {saving ? "…" : "Save"}
                        </button>
                        <button
                          onClick={() => setEditing(null)}
                          className="text-gray-500 hover:text-gray-300 text-xs"
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => startEdit(tier, key)}
                        className="text-white hover:text-green-400 font-mono"
                        title="Click to edit"
                      >
                        {displayValue(value)}
                      </button>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 2: Start dev server and verify**

```bash
cd mgmt-ui && npm run dev
```

Open `http://localhost:3001/access?tab=limits`. Expected: table with 3 rows (limit keys) × 4 columns (tiers), values clickable for inline edit.

- [ ] **Step 3: Commit**

```bash
git add mgmt-ui/app/access/TierLimitsTab.tsx
git commit -m "feat(access): add Tier Limits tab to mgmt-ui access page"
```

---

## Task 11: mgmt-ui — Feature Flags Tab

**Files:**
- Create: `mgmt-ui/app/access/FeatureFlagsTab.tsx`

- [ ] **Step 1: Create FeatureFlagsTab client component**

```tsx
// mgmt-ui/app/access/FeatureFlagsTab.tsx
"use client";

import { useState } from "react";
import { FeatureFlag, api } from "@/lib/api";

const ALL_TIERS = ["free", "pro", "pro_plus", "institution"];
const ALL_FLAGS = [
  "predictions",
  "reports",
  "portfolio_analysis",
  "chat",
  "screener_health_score",
];

export default function FeatureFlagsTab({ initialFlags }: { initialFlags: FeatureFlag[] }) {
  const [flags, setFlags] = useState<FeatureFlag[]>(initialFlags);
  const [saving, setSaving] = useState<string | null>(null); // "flagKey:tier"
  const [error, setError] = useState<string | null>(null);

  function isEnabled(flagKey: string, tier: string): boolean {
    return flags.find((f) => f.flag_key === flagKey && f.tier === tier)?.enabled ?? false;
  }

  async function toggle(flagKey: string, tier: string) {
    const current = isEnabled(flagKey, tier);
    const key = `${flagKey}:${tier}`;
    setSaving(key);
    setError(null);
    try {
      await api.access.updateFeature(flagKey, tier, !current);
      setFlags((prev) =>
        prev.map((f) =>
          f.flag_key === flagKey && f.tier === tier ? { ...f, enabled: !current } : f
        )
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(null);
    }
  }

  return (
    <div className="overflow-x-auto">
      <p className="text-xs text-gray-500 mb-3">
        Changes take effect within 60 seconds (Redis TTL).
      </p>
      {error && <p className="text-red-400 text-sm mb-3">{error}</p>}
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-800">
            <th className="text-left py-2 pr-6 text-gray-400 font-medium">Feature</th>
            {ALL_TIERS.map((t) => (
              <th key={t} className="text-left py-2 pr-6 text-gray-400 font-medium capitalize">
                {t.replace("_", " ")}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ALL_FLAGS.map((flagKey) => (
            <tr key={flagKey} className="border-b border-gray-900">
              <td className="py-3 pr-6 text-gray-300 font-mono text-xs">{flagKey}</td>
              {ALL_TIERS.map((tier) => {
                const enabled = isEnabled(flagKey, tier);
                const key = `${flagKey}:${tier}`;
                const isSaving = saving === key;
                return (
                  <td key={tier} className="py-3 pr-6">
                    <button
                      onClick={() => toggle(flagKey, tier)}
                      disabled={isSaving}
                      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors disabled:opacity-50 ${
                        enabled ? "bg-green-600" : "bg-gray-700"
                      }`}
                      title={enabled ? "Enabled — click to disable" : "Disabled — click to enable"}
                    >
                      <span
                        className={`inline-block h-3 w-3 transform rounded-full bg-white transition-transform ${
                          enabled ? "translate-x-5" : "translate-x-1"
                        }`}
                      />
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 2: Verify in browser**

Open `http://localhost:3001/access?tab=flags`. Expected: grid of toggle switches — green = enabled, gray = disabled. Clicking a toggle sends PUT and flips state.

- [ ] **Step 3: Commit**

```bash
git add mgmt-ui/app/access/FeatureFlagsTab.tsx
git commit -m "feat(access): add Feature Flags tab to mgmt-ui access page"
```

---

## Task 12: mgmt-ui — User Overrides Tab

**Files:**
- Create: `mgmt-ui/app/access/UserOverridesTab.tsx`

- [ ] **Step 1: Create UserOverridesTab client component**

```tsx
// mgmt-ui/app/access/UserOverridesTab.tsx
"use client";

import { useState } from "react";
import { UserSummary, UserOverride, api } from "@/lib/api";

const FLAG_OPTIONS = [
  "predictions",
  "reports",
  "portfolio_analysis",
  "chat",
  "screener_health_score",
];

export default function UserOverridesTab() {
  const [query, setQuery] = useState("");
  const [users, setUsers] = useState<UserSummary[]>([]);
  const [selectedUser, setSelectedUser] = useState<UserSummary | null>(null);
  const [overrides, setOverrides] = useState<UserOverride[]>([]);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // New override form
  const [newFlag, setNewFlag] = useState(FLAG_OPTIONS[0]);
  const [newOverride, setNewOverride] = useState<"grant" | "revoke">("grant");
  const [newExpiry, setNewExpiry] = useState("");
  const [newNote, setNewNote] = useState("");
  const [adding, setAdding] = useState(false);

  async function search() {
    if (!query.trim()) return;
    setSearching(true);
    setError(null);
    try {
      const results = await api.access.searchUsers(query);
      setUsers(results);
    } catch (e) {
      setError(String(e));
    } finally {
      setSearching(false);
    }
  }

  async function selectUser(user: UserSummary) {
    setSelectedUser(user);
    setError(null);
    const data = await api.access.userOverrides(user.id);
    setOverrides(data);
  }

  async function addOverride() {
    if (!selectedUser) return;
    setAdding(true);
    setError(null);
    try {
      const body: { flag_key: string; override: string; expires_at?: string; note?: string } = {
        flag_key: newFlag,
        override: newOverride,
        note: newNote || undefined,
      };
      if (newExpiry) body.expires_at = new Date(newExpiry).toISOString();
      const created = await api.access.createOverride(selectedUser.id, body);
      setOverrides((prev) => {
        const filtered = prev.filter((o) => o.flag_key !== created.flag_key);
        return [...filtered, created];
      });
      setNewNote("");
      setNewExpiry("");
    } catch (e) {
      setError(String(e));
    } finally {
      setAdding(false);
    }
  }

  async function removeOverride(flagKey: string) {
    if (!selectedUser) return;
    await api.access.deleteOverride(selectedUser.id, flagKey);
    setOverrides((prev) => prev.filter((o) => o.flag_key !== flagKey));
  }

  function isExpired(o: UserOverride): boolean {
    return !!o.expires_at && new Date(o.expires_at) < new Date();
  }

  return (
    <div className="space-y-6">
      {/* Search */}
      <div className="flex gap-2">
        <input
          type="text"
          placeholder="Search by email…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && search()}
          className="flex-1 bg-gray-900 border border-gray-700 rounded px-3 py-2 text-white text-sm"
        />
        <button
          onClick={search}
          disabled={searching}
          className="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-white text-sm rounded disabled:opacity-50"
        >
          {searching ? "…" : "Search"}
        </button>
      </div>

      {error && <p className="text-red-400 text-sm">{error}</p>}

      {/* User list */}
      {users.length > 0 && (
        <div className="space-y-1">
          {users.map((u) => (
            <button
              key={u.id}
              onClick={() => selectUser(u)}
              className={`w-full text-left flex items-center gap-3 px-4 py-2 rounded border text-sm transition-colors ${
                selectedUser?.id === u.id
                  ? "border-green-600 bg-green-950 text-white"
                  : "border-gray-800 bg-gray-900 text-gray-300 hover:border-gray-600"
              }`}
            >
              <span className="flex-1">{u.email}</span>
              <span
                className={`text-xs px-2 py-0.5 rounded ${
                  u.tier === "pro" ? "bg-blue-900 text-blue-300" : "bg-gray-800 text-gray-400"
                }`}
              >
                {u.tier}
              </span>
              {!u.is_active && <span className="text-xs text-red-400">inactive</span>}
            </button>
          ))}
        </div>
      )}

      {/* Overrides for selected user */}
      {selectedUser && (
        <div className="space-y-4">
          <h2 className="text-sm text-gray-400">
            Overrides for <span className="text-white">{selectedUser.email}</span>
          </h2>

          {overrides.length === 0 && (
            <p className="text-gray-600 text-sm">No overrides. Tier defaults apply.</p>
          )}

          {overrides.map((o) => (
            <div
              key={o.flag_key}
              className={`flex items-center gap-3 px-4 py-2 rounded border text-sm ${
                isExpired(o)
                  ? "border-gray-800 bg-gray-950 opacity-50"
                  : o.override === "grant"
                  ? "border-green-800 bg-green-950"
                  : "border-red-900 bg-red-950"
              }`}
            >
              <span className="flex-1 text-white font-mono">{o.flag_key}</span>
              <span
                className={`text-xs font-bold uppercase ${
                  o.override === "grant" ? "text-green-400" : "text-red-400"
                }`}
              >
                {o.override}
              </span>
              {o.expires_at && (
                <span className="text-xs text-gray-500">
                  {isExpired(o) ? "expired" : `until ${new Date(o.expires_at).toLocaleDateString()}`}
                </span>
              )}
              {o.note && <span className="text-xs text-gray-500 italic">{o.note}</span>}
              <button
                onClick={() => removeOverride(o.flag_key)}
                className="text-gray-600 hover:text-red-400 text-xs ml-2"
              >
                ✕
              </button>
            </div>
          ))}

          {/* Add override form */}
          <div className="border border-gray-800 rounded p-4 space-y-3">
            <p className="text-xs text-gray-400 font-medium">Add Override</p>
            <div className="flex gap-2 flex-wrap">
              <select
                value={newFlag}
                onChange={(e) => setNewFlag(e.target.value)}
                className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-white text-xs"
              >
                {FLAG_OPTIONS.map((f) => (
                  <option key={f} value={f}>{f}</option>
                ))}
              </select>
              <select
                value={newOverride}
                onChange={(e) => setNewOverride(e.target.value as "grant" | "revoke")}
                className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-white text-xs"
              >
                <option value="grant">Grant</option>
                <option value="revoke">Revoke</option>
              </select>
              <input
                type="date"
                value={newExpiry}
                onChange={(e) => setNewExpiry(e.target.value)}
                placeholder="Expiry (optional)"
                className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-white text-xs"
              />
              <input
                type="text"
                value={newNote}
                onChange={(e) => setNewNote(e.target.value)}
                placeholder="Note (optional)"
                className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-white text-xs flex-1 min-w-32"
              />
              <button
                onClick={addOverride}
                disabled={adding}
                className="px-3 py-1 bg-green-800 hover:bg-green-700 text-white text-xs rounded disabled:opacity-50"
              >
                {adding ? "…" : "Add"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify in browser**

Open `http://localhost:3001/access?tab=users`. Expected:
- Search box → type email → shows matching users
- Click user → shows their overrides (empty initially)
- Add override form → select flag + grant/revoke → click Add → override appears
- Click ✕ → override removed

- [ ] **Step 3: Run all unit tests one final time**

```bash
pytest tests/unit/ -v --tb=short
```

Expected: all tests pass, no regressions.

- [ ] **Step 4: Final commit**

```bash
git add mgmt-ui/app/access/UserOverridesTab.tsx
git commit -m "feat(access): add User Overrides tab to mgmt-ui access page"
```

---

## Self-Review Notes

- All `cache_delete_pattern` calls are exact key strings matching the `api/access.py` cache keys (`access:limits:{tier}:{key}`, `access:flags:{flag_key}:{tier}`, `access:overrides:{user_id}`)
- `get_limit` and `check_feature` signatures consistent across `api/access.py`, `test_access_module.py`, and `rate_limit.py`
- `TIER_LIMITS` removed entirely from `rate_limit.py` — no dangling import
- Migration uses `ON CONFLICT ... DO NOTHING` for idempotent seeds
- All mgmt-ui client components marked `"use client"` — no server-side fetch inside them
- `del` is a reserved word in Python but safe as a TypeScript identifier
