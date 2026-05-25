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
