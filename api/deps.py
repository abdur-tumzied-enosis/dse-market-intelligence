# api/deps.py
from __future__ import annotations
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from api.auth.jwt import decode_token
from api.auth.service import get_user_by_id
from db.pool import get_pool

_bearer = HTTPBearer(auto_error=False)


async def get_db():
    return await get_pool()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    pool=Depends(get_db),
) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(credentials.credentials)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token")
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Not an access token")
    user = await get_user_by_id(pool, int(payload["sub"]))
    if not user or not user["is_active"]:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def require_tier(*allowed_tiers: str):
    """Dependency factory — checks user['tier'] is in allowed_tiers."""
    async def _check(user: dict = Depends(get_current_user)) -> dict:
        if user["tier"] not in allowed_tiers:
            raise HTTPException(
                status_code=403,
                detail=f"Requires tier: {' or '.join(allowed_tiers)}",
            )
        return user
    return _check
