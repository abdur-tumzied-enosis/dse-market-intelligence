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
