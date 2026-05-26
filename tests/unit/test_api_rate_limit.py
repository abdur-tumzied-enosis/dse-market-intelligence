# tests/unit/test_api_rate_limit.py
import os
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse
from api.middleware.rate_limit import RateLimitMiddleware
from api.auth.jwt import create_access_token


def _make_pool_with_limit(limit_value: int):
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
    redis_mock.get = AsyncMock(return_value="50")
    redis_mock.incr = AsyncMock(return_value=1)
    redis_mock.expire = AsyncMock()
    token = create_access_token(1, "a@b.com", "free")
    app = _make_app(redis_mock)
    client = TestClient(app)
    resp = client.get("/api/stocks", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_blocks_when_over_limit():
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value="50")
    redis_mock.incr = AsyncMock(return_value=51)
    redis_mock.expire = AsyncMock()
    token = create_access_token(1, "a@b.com", "free")
    app = _make_app(redis_mock)
    client = TestClient(app)
    resp = client.get("/api/stocks", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 429


def test_institution_tier_skipped_when_limit_zero():
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value="0")
    redis_mock.incr = AsyncMock()
    token = create_access_token(1, "admin@b.com", "institution")
    app = _make_app(redis_mock)
    client = TestClient(app)
    resp = client.get("/api/stocks", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    redis_mock.incr.assert_not_called()


def test_limit_loaded_from_db_on_cache_miss():
    call_count = 0

    async def _redis_get(key):
        nonlocal call_count
        call_count += 1
        if "access:limits" in key:
            return None
        return None

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
