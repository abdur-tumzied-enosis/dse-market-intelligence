# tests/integration/test_api_flow.py
"""
Integration tests — require running PostgreSQL + Redis.
Skip automatically when DATABASE_URL is not set or not reachable.

Run: pytest tests/integration/test_api_flow.py -v
"""
import os
import pytest

DATABASE_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="No DATABASE_URL — skip integration tests",
)


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from api.main import app
    with TestClient(app) as c:
        yield c


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_register_login_access_protected(client):
    # Register (409 if re-running)
    reg = client.post("/api/auth/register", json={
        "email": "integ_test@example.com",
        "password": "TestPass123!",
        "full_name": "Integration Test User",
    })
    assert reg.status_code in (201, 409)

    # Login
    login = client.post("/api/auth/login", json={
        "email": "integ_test@example.com",
        "password": "TestPass123!",
    })
    assert login.status_code == 200
    tokens = login.json()
    assert "access_token" in tokens
    assert "refresh_token" in tokens

    # Access protected endpoint
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    stocks = client.get("/api/stocks?limit=5", headers=headers)
    assert stocks.status_code == 200
    assert "items" in stocks.json()


def test_refresh_token_flow(client):
    login = client.post("/api/auth/login", json={
        "email": "integ_test@example.com",
        "password": "TestPass123!",
    })
    tokens = login.json()

    refresh = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh.status_code == 200
    new_tokens = refresh.json()
    assert "access_token" in new_tokens

    headers = {"Authorization": f"Bearer {new_tokens['access_token']}"}
    resp = client.get("/api/stocks?limit=1", headers=headers)
    assert resp.status_code == 200


def test_unauthenticated_returns_401(client):
    resp = client.get("/api/stocks")
    assert resp.status_code == 401


def test_market_summary_authenticated(client):
    login = client.post("/api/auth/login", json={
        "email": "integ_test@example.com",
        "password": "TestPass123!",
    })
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    resp = client.get("/api/market/summary", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "advance" in body
    assert "total_stocks" in body
