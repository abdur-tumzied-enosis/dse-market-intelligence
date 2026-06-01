from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_current_user, get_db


def _client(conn):
    from api.routers.sectors import router
    app = FastAPI()
    app.include_router(router, prefix="/api")
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=conn)
    acquire.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire)
    app.dependency_overrides[get_db] = lambda: pool
    app.dependency_overrides[get_current_user] = lambda: {"id": 1, "tier": "free"}
    return TestClient(app)


def test_get_sector_detail_returns_history_and_companies():
    conn = MagicMock()
    pe_rows = [{
        "sector": "Telecom", "pe": Decimal("15.5"), "change_pct": Decimal("1.2"),
        "market_cap_bdt": Decimal("1000"), "fetched_at": dt.datetime(2026, 1, 1),
    }]
    conn.fetch = AsyncMock(side_effect=[pe_rows, [{"ticker": "GP"}, {"ticker": "ROBI"}]])
    with patch("api.routers.sectors._cache_get", new=AsyncMock(return_value=None)), \
         patch("api.routers.sectors._cache_set", new=AsyncMock()):
        resp = _client(conn).get("/api/sectors/Telecom")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sector"] == "Telecom"
    assert body["latest_pe"]["pe"] == "15.5"
    assert body["companies"] == ["GP", "ROBI"]


def test_get_sector_detail_404_when_unknown():
    conn = MagicMock()
    conn.fetch = AsyncMock(side_effect=[[], []])
    with patch("api.routers.sectors._cache_get", new=AsyncMock(return_value=None)), \
         patch("api.routers.sectors._cache_set", new=AsyncMock()):
        resp = _client(conn).get("/api/sectors/Nope")
    assert resp.status_code == 404
