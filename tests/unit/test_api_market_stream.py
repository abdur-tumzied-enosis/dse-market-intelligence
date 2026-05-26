import os
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

import json
from unittest.mock import AsyncMock, patch
import pytest
from api.routers.market import _generate_market_events


FAKE_INDICES = {
    "dsex_value": 5330.89, "dsex_change_pct": 1.27,
    "ds30_value": 2023.89, "ds30_change_pct": 1.43,
    "dses_value": 1078.43, "dses_change_pct": 0.84,
    "market_status": "Open", "advance": 271, "decline": 67, "unchanged": 68,
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
