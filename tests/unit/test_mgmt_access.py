# tests/unit/test_mgmt_access.py
import os
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mgmt.routers.access import router


def _make_pool(rows=None, execute_ok=True):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows or [])
    conn.fetchrow = AsyncMock(return_value=rows[0] if rows else None)
    conn.execute = AsyncMock(return_value="UPDATE 1" if execute_ok else "UPDATE 0")
    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


def _make_app(pool):
    app = FastAPI()
    app.include_router(router)

    async def _get_db():
        return pool

    from mgmt.deps import get_db
    app.dependency_overrides[get_db] = _get_db
    return app


# ── Tier Limits ──────────────────────────────────────────────────────

def test_list_tier_limits_returns_rows():
    rows = [
        {"tier": "free", "limit_key": "api_calls_per_day", "limit_value": 50, "updated_at": None},
        {"tier": "pro",  "limit_key": "api_calls_per_day", "limit_value": 1000, "updated_at": None},
    ]
    pool, _ = _make_pool(rows)
    client = TestClient(_make_app(pool))

    with patch("mgmt.routers.access.cache_delete_pattern", new=AsyncMock()):
        resp = client.get("/mgmt/access/tier-limits")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["tier"] == "free"


def test_update_tier_limit_writes_and_invalidates():
    pool, conn = _make_pool()
    client = TestClient(_make_app(pool))

    with patch("mgmt.routers.access.cache_delete_pattern", new=AsyncMock()) as mock_del:
        resp = client.put(
            "/mgmt/access/tier-limits/free/api_calls_per_day",
            json={"limit_value": 100},
        )

    assert resp.status_code == 200
    conn.execute.assert_called_once()
    mock_del.assert_called_once_with("access:limits:free:api_calls_per_day")


def test_update_tier_limit_rejects_negative():
    pool, _ = _make_pool()
    client = TestClient(_make_app(pool))

    resp = client.put(
        "/mgmt/access/tier-limits/free/api_calls_per_day",
        json={"limit_value": -5},
    )

    assert resp.status_code == 422


# ── Feature Flags ────────────────────────────────────────────────────

def test_list_feature_flags_returns_rows():
    rows = [
        {"flag_key": "predictions", "tier": "free", "enabled": False, "updated_at": None},
        {"flag_key": "predictions", "tier": "pro",  "enabled": True,  "updated_at": None},
    ]
    pool, _ = _make_pool(rows)
    client = TestClient(_make_app(pool))

    resp = client.get("/mgmt/access/features")

    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_update_feature_flag_writes_and_invalidates():
    pool, conn = _make_pool()
    client = TestClient(_make_app(pool))

    with patch("mgmt.routers.access.cache_delete_pattern", new=AsyncMock()) as mock_del:
        resp = client.put(
            "/mgmt/access/features/predictions/free",
            json={"enabled": True},
        )

    assert resp.status_code == 200
    conn.execute.assert_called_once()
    mock_del.assert_called_once_with("access:flags:predictions:free")
