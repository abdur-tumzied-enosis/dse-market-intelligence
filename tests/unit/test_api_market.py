# tests/unit/test_api_market.py
import os

os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import deps


def _make_app(routers):
    app = FastAPI()
    for r in routers:
        app.include_router(r, prefix="/api")

    async def _get_db():
        return None  # overridden per-test

    async def _get_user():
        return {"id": 1, "email": "a@b.com", "tier": "free", "is_active": True}

    app.dependency_overrides[deps.get_current_user] = _get_user
    return app


def _pool_with_fetchrow(return_value):
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=return_value)
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_pool


def _pool_with_fetch(return_values):
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(side_effect=return_values)
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_pool


# ---- Market tests ----

def test_market_summary_returns_aggregates():
    from api.routers.market import router as market_router
    pool = _pool_with_fetchrow({
        "total_stocks": 400, "advance": 200, "decline": 150, "unchanged": 50,
        "total_volume": 10000000, "total_value_bdt": Decimal("5000000000"),
        "avg_change_pct": Decimal("0.5"),
    })
    app = _make_app([market_router])
    app.dependency_overrides[deps.get_db] = lambda: pool

    with patch("api.routers.market._cache_get", return_value=None), \
         patch("api.routers.market._cache_set"):
        client = TestClient(app)
        resp = client.get("/api/market/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["advance"] == 200
    assert body["total_stocks"] == 400


def test_market_movers_returns_gainers_and_losers():
    """Movers come from the live_prices stream; names are enriched from companies."""
    from api.routers.market import router as market_router
    # single conn.fetch → companies map (ticker → name/sector)
    pool = _pool_with_fetch([
        [{"ticker": "GP", "name": "Grameenphone", "sector": "Telecom"},
         {"ticker": "SQURPHARMA", "name": "Square Pharma", "sector": "Pharma"}],
    ])
    app = _make_app([market_router])
    app.dependency_overrides[deps.get_db] = lambda: pool

    live = [
        {"ticker": "GP", "close": 400.0, "change_pct": 5.0},
        {"ticker": "SQURPHARMA", "close": 200.0, "change_pct": -3.0},
    ]
    with patch("api.routers.market._cache_get", return_value=None), \
         patch("api.routers.market._cache_set"), \
         patch("api.routers.market._fetch_live_records", return_value=live):
        client = TestClient(app)
        resp = client.get("/api/market/movers")
    assert resp.status_code == 200
    body = resp.json()
    assert body["gainers"][0]["ticker"] == "GP"
    assert body["gainers"][0]["name"] == "Grameenphone"
    assert body["losers"][0]["ticker"] == "SQURPHARMA"


def test_market_movers_drops_non_finite_floats():
    """NaN/Infinity from a source must not reach the response — Starlette renders
    with allow_nan=False, so one bad float 500s the whole request."""
    from api.routers.market import router as market_router
    pool = _pool_with_fetch([[{"ticker": "GP", "name": "Grameenphone", "sector": "Telecom"}]])
    app = _make_app([market_router])
    app.dependency_overrides[deps.get_db] = lambda: pool

    live = [
        {"ticker": "GP", "close": 400.0, "change_pct": 5.0},
        {"ticker": "NANCO", "close": 100.0, "change_pct": float("nan")},
        {"ticker": "INFCO", "close": float("inf"), "change_pct": 2.0},
    ]
    with patch("api.routers.market._cache_get", return_value=None), \
         patch("api.routers.market._cache_set"), \
         patch("api.routers.market._fetch_live_records", return_value=live):
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/api/market/movers")
    assert resp.status_code == 200  # would be 500 if NaN/inf leaked through
    tickers = {m["ticker"] for m in resp.json()["gainers"]}
    assert tickers == {"GP"}  # NANCO and INFCO dropped


def test_rec_change_pct_derives_from_prev_close_when_absent():
    """dse_direct rows omit change_pct — helper derives it from ltp/prev_close."""
    from api.routers.market import _rec_change_pct
    # present → passthrough
    assert _rec_change_pct({"change_pct": 2.5}) == 2.5
    # absent → computed from ltp + prev_close
    cp = _rec_change_pct({"change_pct": None, "ltp": 29.5, "prev_close": 28.7})
    assert abs(cp - (29.5 - 28.7) / 28.7 * 100.0) < 1e-9
    # no basis → None
    assert _rec_change_pct({"change_pct": None, "ltp": 29.5}) is None
    assert _rec_change_pct({"change_pct": None, "ltp": 29.5, "prev_close": 0}) is None


# ---- Sector tests ----

def test_list_sectors_returns_rows():
    from api.routers.sectors import router as sectors_router
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[
        {"sector": "Telecom", "pe": Decimal("15.5"), "change_pct": Decimal("1.2"),
         "market_cap_bdt": Decimal("1000000000"), "fetched_at": datetime.now(UTC)}
    ])
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    app = _make_app([sectors_router])
    app.dependency_overrides[deps.get_db] = lambda: mock_pool

    with patch("api.routers.sectors._cache_get", return_value=None), \
         patch("api.routers.sectors._cache_set"):
        client = TestClient(app)
        resp = client.get("/api/sectors")
    assert resp.status_code == 200
    assert resp.json()[0]["sector"] == "Telecom"


# ---- Analyze tests ----

def test_analyze_ticker_not_found_returns_404():
    from api.routers.analyze import router as analyze_router
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    app = _make_app([analyze_router])
    app.dependency_overrides[deps.get_db] = lambda: mock_pool

    client = TestClient(app)
    resp = client.get("/api/analyze/NOTEXIST")
    assert resp.status_code == 404


def test_compare_requires_at_least_two_tickers():
    from api.routers.analyze import router as analyze_router
    app = _make_app([analyze_router])
    app.dependency_overrides[deps.get_db] = lambda: MagicMock()

    client = TestClient(app)
    resp = client.get("/api/compare?tickers=GP")
    assert resp.status_code == 422
