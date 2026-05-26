# tests/unit/test_api_market_indices.py
import os
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.routers.market import router, _fetch_indices_from_amarstock


FAKE_AMARSTOCK = {
    "IndexValue": 5330.89, "Change": 66.77, "ChangePct": 1.27,
    "DsIndex": 1078.43, "DsChange": 8.95, "DsChangePct": 0.84,
    "D30Index": 2023.89, "D30Change": 28.53, "D30ChangePct": 1.43,
    "TotalTrade": 142982, "TotalVolume": 187312467, "TotalValue": 5816.28,
    "Advance": 271, "Decline": 67, "Unchange": 68, "MarketStatus": "Open",
}


def _mock_httpx(json_data: dict):
    mock_resp = MagicMock()
    mock_resp.json.return_value = json_data
    mock_resp.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)
    return mock_client


@pytest.mark.asyncio
async def test_fetch_indices_from_amarstock_maps_fields():
    with patch("api.routers.market.httpx.AsyncClient", return_value=_mock_httpx(FAKE_AMARSTOCK)):
        result = await _fetch_indices_from_amarstock()

    assert result["dsex_value"] == pytest.approx(5330.89)
    assert result["dsex_change_pct"] == pytest.approx(1.27)
    assert result["ds30_value"] == pytest.approx(2023.89)
    assert result["dses_value"] == pytest.approx(1078.43)
    assert result["market_status"] == "Open"
    assert result["advance"] == 271
    assert result["decline"] == 67
    assert result["unchanged"] == 68
    assert result["ds30_change_pct"] == pytest.approx(1.43)
    assert result["dses_change_pct"] == pytest.approx(0.84)


@pytest.mark.asyncio
async def test_fetch_indices_from_amarstock_raises_on_http_error():
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = Exception("HTTP 503")
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)
    with patch("api.routers.market.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(Exception, match="HTTP 503"):
            await _fetch_indices_from_amarstock()
