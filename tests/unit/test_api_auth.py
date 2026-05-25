import os
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

import pytest
from api.auth.jwt import create_access_token, create_refresh_token, decode_token


def test_access_token_round_trip():
    token = create_access_token(user_id=1, email="a@b.com", tier="free")
    payload = decode_token(token)
    assert payload["sub"] == "1"
    assert payload["email"] == "a@b.com"
    assert payload["tier"] == "free"
    assert payload["type"] == "access"


def test_refresh_token_round_trip():
    token = create_refresh_token(user_id=42)
    payload = decode_token(token)
    assert payload["sub"] == "42"
    assert payload["type"] == "refresh"


def test_invalid_token_raises():
    with pytest.raises(ValueError, match="Invalid token"):
        decode_token("not-a-token")


def test_tampered_token_raises():
    token = create_access_token(1, "a@b.com", "free")
    with pytest.raises(ValueError):
        decode_token(token + "tamper")


import asyncio
from unittest.mock import AsyncMock, MagicMock
from api.auth.service import hash_password, verify_password, create_user, authenticate_user, get_user_by_id


def test_hash_and_verify():
    hashed = hash_password("mysecretpass")
    assert verify_password("mysecretpass", hashed)
    assert not verify_password("wrongpass", hashed)


@pytest.mark.asyncio
async def test_create_user_returns_row():
    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {
        "id": 1, "email": "a@b.com", "full_name": None,
        "tier": "free", "is_active": True, "created_at": None,
    }
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    result = await create_user(mock_pool, "a@b.com", "password123")
    assert result["email"] == "a@b.com"
    assert result["id"] == 1


@pytest.mark.asyncio
async def test_authenticate_user_wrong_password_returns_none():
    hashed = hash_password("correctpass")
    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {
        "id": 1, "email": "a@b.com", "hashed_password": hashed,
        "full_name": None, "tier": "free", "is_active": True,
    }
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    result = await authenticate_user(mock_pool, "a@b.com", "wrongpass")
    assert result is None


@pytest.mark.asyncio
async def test_authenticate_user_correct_password():
    hashed = hash_password("correctpass")
    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {
        "id": 1, "email": "a@b.com", "hashed_password": hashed,
        "full_name": None, "tier": "free", "is_active": True,
    }
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    result = await authenticate_user(mock_pool, "a@b.com", "correctpass")
    assert result is not None
    assert result["email"] == "a@b.com"
