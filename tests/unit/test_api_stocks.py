# tests/unit/test_api_stocks.py
import os

os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import deps
from api.routers.stocks import router


def _make_app(pool_mock):
    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def _get_db():
        return pool_mock

    async def _get_user():
        return {"id": 1, "email": "a@b.com", "tier": "free", "is_active": True}

    app.dependency_overrides[deps.get_db] = _get_db
    app.dependency_overrides[deps.get_current_user] = _get_user
    return app


def _pool_with(fetch_return=None, fetchrow_return=None, fetchval_return=0):
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=fetch_return or [])
    mock_conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    mock_conn.fetchval = AsyncMock(return_value=fetchval_return)
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_pool


def test_list_stocks_returns_paged_response():
    pool = _pool_with(
        fetch_return=[{
            "ticker": "GP", "name": "Grameenphone", "sector": "Telecom",
            "category": "A", "market_cap_bdt": Decimal("100000"), "is_active": True
        }],
        fetchval_return=1,
    )
    with patch("api.routers.stocks._cache_get", return_value=None), \
         patch("api.routers.stocks._cache_set"):
        client = TestClient(_make_app(pool))
        resp = client.get("/api/stocks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["ticker"] == "GP"


def test_get_stock_not_found_returns_404():
    pool = _pool_with(fetchrow_return=None)
    with patch("api.routers.stocks._cache_get", return_value=None), \
         patch("api.routers.stocks._cache_set"):
        client = TestClient(_make_app(pool))
        resp = client.get("/api/stocks/NOTEXIST")
    assert resp.status_code == 404


def test_get_stock_returns_detail():
    mock_conn = AsyncMock()
    # fetchrow called 4 times: company, latest_price, fundamentals, health_score
    mock_conn.fetchrow = AsyncMock(side_effect=[
        {"ticker": "GP", "name": "Grameenphone", "sector": "Telecom", "category": "A",
         "market_cap_bdt": Decimal("100000"), "is_active": True, "listing_date": None, "isin": None},
        {"close": Decimal("400"), "change_pct": Decimal("1.5"), "volume": 100000,
         "value_bdt": Decimal("40000000"), "high": Decimal("405"), "low": Decimal("395"),
         "time": datetime(2026, 1, 1, tzinfo=UTC)},
        None,  # no fundamentals
        None,  # no health score
    ])
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("api.routers.stocks._cache_get", return_value=None), \
         patch("api.routers.stocks._cache_set"):
        client = TestClient(_make_app(mock_pool))
        resp = client.get("/api/stocks/gp")
    assert resp.status_code == 200
    body = resp.json()
    assert body["company"]["ticker"] == "GP"
    assert body["latest_price"]["close"] == "400"
    assert body["fundamentals"] is None


def test_list_stocks_includes_pe_health_and_rating():
    rows = [{
        "ticker": "GP", "name": "Grameenphone", "sector": "Telecom",
        "category": "A", "market_cap_bdt": Decimal("1000"), "is_active": True,
        "pe": Decimal("12.5"), "health_score": Decimal("82"),
        "last_close": Decimal("300.5"), "change_pct": Decimal("1.2"),
    }]
    pool = _pool_with(fetch_return=rows, fetchval_return=1)
    with patch("api.routers.stocks._cache_get", new=AsyncMock(return_value=None)), \
         patch("api.routers.stocks._cache_set", new=AsyncMock()):
        client = TestClient(_make_app(pool))
        resp = client.get("/api/stocks?limit=50&offset=0")
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["ticker"] == "GP"
    assert item["pe"] == "12.5"
    assert item["health_score"] == "82"
    assert item["rating"] == "STRONG_BUY"  # health 82 >= 80


def test_list_stocks_rating_null_when_no_score():
    rows = [{
        "ticker": "XX", "name": "X Co", "sector": "Misc",
        "category": None, "market_cap_bdt": None, "is_active": True,
        "pe": None, "health_score": None, "last_close": None, "change_pct": None,
    }]
    pool = _pool_with(fetch_return=rows, fetchval_return=1)
    with patch("api.routers.stocks._cache_get", new=AsyncMock(return_value=None)), \
         patch("api.routers.stocks._cache_set", new=AsyncMock()):
        client = TestClient(_make_app(pool))
        resp = client.get("/api/stocks")
    assert resp.json()["items"][0]["rating"] == "N/A"


def test_get_prices_returns_ohlcv():
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value={"ticker": "GP"})  # exists check
    mock_conn.fetch = AsyncMock(return_value=[{
        "day": "2026-01-01", "open": Decimal("400"), "high": Decimal("410"),
        "low": Decimal("395"), "close": Decimal("405"),
        "volume": 100000, "value_bdt": Decimal("41000000"),
    }])
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("api.routers.stocks._cache_get", return_value=None), \
         patch("api.routers.stocks._cache_set"):
        client = TestClient(_make_app(mock_pool))
        resp = client.get("/api/stocks/GP/prices")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticker"] == "GP"
    assert body["interval"] == "daily"
    assert len(body["items"]) == 1
