# tests/unit/test_access_module.py
import os
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

import pytest
from unittest.mock import AsyncMock, MagicMock
from contextlib import asynccontextmanager


def _make_pool(rows: list[dict]):
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
