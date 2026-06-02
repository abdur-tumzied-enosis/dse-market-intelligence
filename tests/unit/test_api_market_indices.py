# tests/unit/test_api_market_indices.py
import os
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from api.routers.market import _build_indices
from extraction.base import AllAdaptersFailedError


def _fake_stream(df: pd.DataFrame):
    s = MagicMock()
    s.fetch = AsyncMock(return_value=SimpleNamespace(data=df))
    return s


@pytest.mark.asyncio
async def test_build_indices_maps_from_registry():
    idx_df = pd.DataFrame([
        {"index_name": "DSEX", "value": 5330.89, "change_pct": 1.27},
        {"index_name": "DS30", "value": 2023.89, "change_pct": 1.43},
        {"index_name": "DSES", "value": 1078.43, "change_pct": 0.84},
    ])
    live_df = pd.DataFrame([
        {"ticker": "A", "change_pct": 2.0},
        {"ticker": "B", "change_pct": -1.0},
        {"ticker": "C", "change_pct": 0.0},
    ])
    streams = {"market_indices": _fake_stream(idx_df), "live_prices": _fake_stream(live_df)}
    with patch.dict("api.routers.market.STREAMS", streams), \
         patch("api.routers.market.get_market_status",
               AsyncMock(return_value={"status": "Closed", "source": "clock"})):
        result = await _build_indices()

    assert result["dsex_value"] == pytest.approx(5330.89)
    assert result["dsex_change_pct"] == pytest.approx(1.27)
    assert result["ds30_value"] == pytest.approx(2023.89)
    assert result["dses_value"] == pytest.approx(1078.43)
    assert result["dses_change_pct"] == pytest.approx(0.84)
    # breadth computed from the live snapshot
    assert result["advance"] == 1
    assert result["decline"] == 1
    assert result["unchanged"] == 1
    assert result["market_status"] in ("Open", "Closed")
    assert result["status_source"] == "clock"


@pytest.mark.asyncio
async def test_build_indices_partial_when_live_breadth_fails():
    """Index values still returned even if the live snapshot (breadth) is down."""
    idx_df = pd.DataFrame([{"index_name": "DSEX", "value": 5330.89, "change_pct": 1.27}])
    bad_live = MagicMock()
    bad_live.fetch = AsyncMock(side_effect=AllAdaptersFailedError("live_prices", []))
    streams = {"market_indices": _fake_stream(idx_df), "live_prices": bad_live}
    with patch.dict("api.routers.market.STREAMS", streams), \
         patch("api.routers.market.get_market_status",
               AsyncMock(return_value={"status": "Closed", "source": "clock"})):
        result = await _build_indices()

    assert result["dsex_value"] == pytest.approx(5330.89)
    assert result["advance"] == 0
    assert result["decline"] == 0
