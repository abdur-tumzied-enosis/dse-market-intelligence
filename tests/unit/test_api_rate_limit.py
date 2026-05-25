import os
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

import pytest
from unittest.mock import AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse
from api.middleware.rate_limit import RateLimitMiddleware, TIER_LIMITS
from api.auth.jwt import create_access_token


def _make_app(redis_mock):
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, get_redis_fn=lambda: redis_mock)

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
    redis_mock.incr = AsyncMock(return_value=1)
    redis_mock.expire = AsyncMock()
    token = create_access_token(1, "a@b.com", "free")
    app = _make_app(redis_mock)
    client = TestClient(app)
    resp = client.get("/api/stocks", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_blocks_when_over_limit():
    redis_mock = AsyncMock()
    redis_mock.incr = AsyncMock(return_value=51)  # free limit is 50
    redis_mock.expire = AsyncMock()
    token = create_access_token(1, "a@b.com", "free")
    app = _make_app(redis_mock)
    client = TestClient(app)
    resp = client.get("/api/stocks", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 429


def test_institution_tier_never_blocked():
    redis_mock = AsyncMock()
    token = create_access_token(1, "admin@b.com", "institution")
    app = _make_app(redis_mock)
    client = TestClient(app)
    resp = client.get("/api/stocks", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    redis_mock.incr.assert_not_called()


def test_tier_limits_correct():
    assert TIER_LIMITS["free"] == 50
    assert TIER_LIMITS["pro"] == 1000
    assert TIER_LIMITS["pro_plus"] == 5000
    assert TIER_LIMITS["institution"] == 0
