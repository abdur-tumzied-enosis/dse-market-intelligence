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

TIER_LIMITS: dict[str, int] = {
    "free": 50,
    "pro": 1000,
    "pro_plus": 5000,
    "institution": 0,  # 0 = unlimited
}

_SKIP_PREFIXES = ("/api/auth/", "/health", "/docs", "/openapi.json", "/redoc")
_ALGORITHM = "HS256"


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, get_redis_fn: Callable | None = None):
        super().__init__(app)
        self._get_redis = get_redis_fn

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
        limit = TIER_LIMITS.get(tier, 50)
        if limit == 0:
            return await call_next(request)

        get_redis = self._get_redis
        if get_redis is None:
            from mgmt.cache import get_redis as _default_get_redis
            get_redis = _default_get_redis

        # Support both sync and async redis getters (sync for tests, async for production)
        if asyncio.iscoroutinefunction(get_redis):
            redis = await get_redis()
        else:
            redis = get_redis()

        user_id = payload.get("sub", "anon")
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
