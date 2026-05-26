"""Redis cache helpers — cache-aside pattern for mgmt API."""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import redis.asyncio as aioredis

_redis: aioredis.Redis | None = None  # type: ignore[type-arg]


async def get_redis() -> aioredis.Redis:  # type: ignore[type-arg]
    global _redis
    if _redis is None:
        from mgmt.config import get_settings
        cfg = get_settings()
        _redis = aioredis.from_url(cfg.redis_url, decode_responses=True)
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None


async def cache_get(key: str) -> Any | None:
    r = await get_redis()
    raw = await r.get(key)
    if raw is None:
        return None
    return json.loads(raw)


async def cache_set(key: str, value: Any, ttl: int) -> None:
    r = await get_redis()
    def _default(obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return float(obj)
        return str(obj)

    await r.set(key, json.dumps(value, default=_default), ex=ttl)


async def cache_delete_pattern(pattern: str) -> int:
    """Delete all keys matching pattern. Uses SCAN — safe in production."""
    r = await get_redis()
    count = 0
    async for key in r.scan_iter(match=pattern):
        await r.delete(key)
        count += 1
    return count
