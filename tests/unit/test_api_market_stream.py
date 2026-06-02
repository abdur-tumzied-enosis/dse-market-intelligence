import os

os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

import json
from unittest.mock import AsyncMock

import pytest

from api.routers.market import _generate_market_events

FAKE_INDICES = {
    "dsex_value": 5330.89, "dsex_change_pct": 1.27,
    "ds30_value": 2023.89, "ds30_change_pct": 1.43,
    "dses_value": 1078.43, "dses_change_pct": 0.84,
    "market_status": "Open", "status_source": "dse_direct",
    "advance": 271, "decline": 67, "unchanged": 68,
}


@pytest.mark.asyncio
async def test_generate_market_events_yields_sse_format():
    async def _fake_get_cached():
        return FAKE_INDICES

    gen = _generate_market_events(get_indices_fn=_fake_get_cached, interval=0)
    event = await gen.__anext__()
    assert event.startswith("data: ")
    payload = json.loads(event[len("data: "):].strip())
    assert payload["dsex_value"] == pytest.approx(5330.89)
    assert payload["market_status"] == "Open"


@pytest.mark.asyncio
async def test_generate_market_events_on_error_yields_error_event():
    async def _fail():
        raise Exception("upstream down")

    gen = _generate_market_events(get_indices_fn=_fail, interval=0)
    event = await gen.__anext__()
    assert event.startswith("data: ")
    payload = json.loads(event[len("data: "):].strip())
    assert payload["error"] == "fetch_failed"


@pytest.mark.asyncio
async def test_generate_market_events_stops_when_client_disconnected():
    """A disconnected client ends the stream immediately — no events, no hang.
    This is what lets the server stop waiting on the SSE connection."""
    async def _fake_get_cached():
        return FAKE_INDICES

    req = AsyncMock()
    req.is_disconnected = AsyncMock(return_value=True)

    events = []
    async for event in _generate_market_events(req, get_indices_fn=_fake_get_cached, interval=30):
        events.append(event)

    assert events == []
    req.is_disconnected.assert_awaited()


@pytest.mark.asyncio
async def test_generate_market_events_stops_when_market_closed():
    """Generator must yield exactly one event then stop when market_status != 'Open'."""
    closed = {**FAKE_INDICES, "market_status": "Closed"}

    async def _fake_closed():
        return closed

    events = []
    async for event in _generate_market_events(get_indices_fn=_fake_closed, interval=0):
        events.append(event)

    assert len(events) == 1
    payload = json.loads(events[0][len("data: "):].strip())
    assert payload["market_status"] == "Closed"
