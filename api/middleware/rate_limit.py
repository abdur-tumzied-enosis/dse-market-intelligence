# api/middleware/rate_limit.py
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date
from typing import Callable

from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from api.config import get_settings

_SKIP_PREFIXES = ("/api/auth/", "/health", "/docs", "/openapi.json", "/redoc")
_ALGORITHM = "HS256"


class _LazyPool:
    """Defers pool resolution until .acquire() is actually called (e.g. on cache miss)."""

    def __init__(self, get_pool_fn: Callable):
        self._get_pool_fn = get_pool_fn

    @asynccontextmanager
    async def acquire(self):
        fn = self._get_pool_fn
        if asyncio.iscoroutinefunction(fn):
            pool = await fn()
        else:
            pool = fn()
        async with pool.acquire() as conn:
            yield conn


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

        # Use a lazy pool so the DB connection is only opened on cache miss
        pool = _LazyPool(get_pool)

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
