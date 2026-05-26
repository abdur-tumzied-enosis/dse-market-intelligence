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


@pytest.mark.asyncio
async def test_check_feature_grant_override_wins():
    from api.access import check_feature
    import json

    pool, conn = _make_pool([])
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
    past = "2020-01-01T00:00:00+00:00"
    overrides_json = json.dumps({"predictions": {"override": "grant", "expires_at": past}})

    call_count = 0

    async def _redis_get(key):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return overrides_json
        return "true"

    redis = AsyncMock()
    redis.get = _redis_get
    redis.set = AsyncMock()

    result = await check_feature(pool, redis, user_id=1, tier="pro", flag_key="predictions")

    assert result is True


@pytest.mark.asyncio
async def test_check_feature_no_override_uses_flag():
    from api.access import check_feature

    pool, conn = _make_pool([])

    call_count = 0

    async def _redis_get(key):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "{}"
        return "true"

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
            return "{}"
        return "false"

    redis = AsyncMock()
    redis.get = _redis_get
    redis.set = AsyncMock()

    result = await check_feature(pool, redis, user_id=3, tier="free", flag_key="predictions")

    assert result is False
