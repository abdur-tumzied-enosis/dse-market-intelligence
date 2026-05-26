# mgmt/routers/access.py
from __future__ import annotations

from fastapi import APIRouter, Depends
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
    expires_at: str | None = None
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
