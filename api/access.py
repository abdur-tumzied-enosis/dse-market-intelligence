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
