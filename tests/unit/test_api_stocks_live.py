import os
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api import deps


def _pool_company_exists(exists):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=({"?column?": 1} if exists else None))
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def _app(pool):
    from api.routers.stocks import router
    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def _get_user():
        return {"id": 1, "email": "a@b.com", "tier": "free", "is_active": True}

    app.dependency_overrides[deps.get_current_user] = _get_user
    app.dependency_overrides[deps.get_db] = lambda: pool
    return app


def test_live_returns_filtered_ticker():
    snapshot = [
        {"ticker": "GP", "ltp": 400.0, "high": 410.0, "low": 395.0,
         "prev_close": 390.0, "change_pct": 2.56, "volume": 1000, "value_bdt": 4e8},
        {"ticker": "CITYBANK", "ltp": 23.4, "change_pct": 1.7},
    ]
    with patch("api.routers.stocks._live_snapshot", AsyncMock(return_value=snapshot)), \
         patch("api.routers.stocks._market_status", return_value="Open"):
        client = TestClient(_app(_pool_company_exists(True)))
        resp = client.get("/api/stocks/GP/live")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticker"] == "GP"
    assert body["available"] is True
    assert body["ltp"] == 400.0
    assert body["market_status"] == "Open"


def test_live_404_for_unknown_ticker():
    with patch("api.routers.stocks._live_snapshot", AsyncMock(return_value=[])), \
         patch("api.routers.stocks._market_status", return_value="Closed"):
        client = TestClient(_app(_pool_company_exists(False)))
        resp = client.get("/api/stocks/NOPE/live")
    assert resp.status_code == 404


def test_live_available_false_when_absent_from_snapshot():
    snapshot = [{"ticker": "GP", "ltp": 400.0}]
    with patch("api.routers.stocks._live_snapshot", AsyncMock(return_value=snapshot)), \
         patch("api.routers.stocks._market_status", return_value="Open"):
        client = TestClient(_app(_pool_company_exists(True)))
        resp = client.get("/api/stocks/CITYBANK/live")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["ltp"] is None
    assert body["ticker"] == "CITYBANK"
