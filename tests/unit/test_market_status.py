import os

os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

import extraction.market_status as ms
import extraction.scheduler as sched
from extraction.adapters.dse_direct.market_status import _parse_status
from extraction.market_status import clock_status, normalize_status
from extraction.normalizers import DHAKA_TZ


def test_normalize_status_maps_labels():
    assert normalize_status("Market Status: Open") == "Open"
    assert normalize_status("Market Status: Closed") == "Closed"
    assert normalize_status("market status : open") == "Open"
    assert normalize_status("Post-Close") == "Closed"
    assert normalize_status(None) == "Closed"
    assert normalize_status("") == "Closed"


def test_clock_status_matches_trading_window():
    # 2026-06-01 is a Monday (trading day)
    assert clock_status(datetime(2026, 6, 1, 11, 0, tzinfo=DHAKA_TZ)) == "Open"
    assert clock_status(datetime(2026, 6, 1, 10, 0, tzinfo=DHAKA_TZ)) == "Open"   # open boundary
    assert clock_status(datetime(2026, 6, 1, 14, 30, tzinfo=DHAKA_TZ)) == "Open"  # close boundary
    assert clock_status(datetime(2026, 6, 1, 9, 59, tzinfo=DHAKA_TZ)) == "Closed"
    assert clock_status(datetime(2026, 6, 1, 15, 0, tzinfo=DHAKA_TZ)) == "Closed"
    assert clock_status(datetime(2026, 6, 5, 11, 0, tzinfo=DHAKA_TZ)) == "Closed"  # Friday
    assert clock_status(datetime(2026, 6, 6, 11, 0, tzinfo=DHAKA_TZ)) == "Closed"  # Saturday


_PAGE_CLOSED = """
<html><body>
  <div class="topbar">Market Status: Closed</div>
  <div class="clock">Tue, 2 Jun, 26 2:31:52 PM Closed</div>
  <div class="LeftColHome">DSEX Index 5330.89</div>
</body></html>
"""

_PAGE_OPEN = """
<html><body><span>Market Status: Open</span></body></html>
"""


def test_parse_status_closed():
    rec = _parse_status(_PAGE_CLOSED)
    assert rec["status"] == "Closed"
    assert "Market Status" in rec["raw_label"]


def test_parse_status_open():
    assert _parse_status(_PAGE_OPEN)["status"] == "Open"


def test_parse_status_missing_raises():
    with pytest.raises(ValueError):
        _parse_status("<html><body>no status here</body></html>")


@pytest.mark.asyncio
async def test_get_market_status_redis_hit():
    payload = {"status": "Open", "source": "dse_direct", "checked_at": "2026-06-02T11:00:00+06:00"}
    with patch.object(ms, "cache_get", AsyncMock(return_value=payload)):
        assert (await ms.get_market_status()) == payload


@pytest.mark.asyncio
async def test_get_market_status_db_fallback():
    row = {"status": "Closed", "source": "dse_direct",
           "checked_at": datetime(2026, 6, 2, 15, 0, tzinfo=DHAKA_TZ)}
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=row)
    with patch.object(ms, "cache_get", AsyncMock(return_value=None)), \
         patch.object(ms, "_get_pool", AsyncMock(return_value=pool)):
        result = await ms.get_market_status()
    assert result["status"] == "Closed"
    assert result["source"] == "dse_direct"


@pytest.mark.asyncio
async def test_get_market_status_clock_fallback():
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    with patch.object(ms, "cache_get", AsyncMock(return_value=None)), \
         patch.object(ms, "_get_pool", AsyncMock(return_value=pool)):
        result = await ms.get_market_status()
    assert result["status"] in ("Open", "Closed")
    assert result["source"] == "clock"


@pytest.mark.asyncio
async def test_refresh_uses_clock_on_scrape_failure():
    from extraction.base import AllAdaptersFailedError
    bad = MagicMock()
    bad.fetch = AsyncMock(side_effect=AllAdaptersFailedError("market_status", []))
    writes = []
    with patch.dict("extraction.registry.STREAMS", {"market_status": bad}, clear=False), \
         patch.object(ms, "_write", AsyncMock(side_effect=lambda r: writes.append(r))):
        rec = await ms.refresh_market_status()
    assert rec["source"] == "clock"
    assert rec["status"] in ("Open", "Closed")
    assert writes and writes[0]["source"] == "clock"


@pytest.mark.asyncio
async def test_refresh_uses_scrape_when_ok():
    df = pd.DataFrame([{"status": "Open", "raw_label": "Market Status: Open"}])
    ok = MagicMock()
    ok.fetch = AsyncMock(return_value=MagicMock(data=df))
    with patch.dict("extraction.registry.STREAMS", {"market_status": ok}, clear=False), \
         patch.object(ms, "_write", AsyncMock()):
        rec = await ms.refresh_market_status()
    assert rec["status"] == "Open"
    assert rec["source"] == "dse_direct"


def test_get_market_status_sync_clock_fallback():
    with patch.object(ms, "_sync_redis", MagicMock(side_effect=RuntimeError("no redis"))):
        result = ms.get_market_status_sync()
    assert result["status"] in ("Open", "Closed")
    assert result["source"] == "clock"


@pytest.mark.asyncio
async def test_job_market_status_open_triggers_live_once():
    """When the morning poll sees Open, job_live_prices fires once (Redis flag guards repeats)."""
    flag_store = {}
    redis = MagicMock()
    redis.get = AsyncMock(side_effect=lambda k: flag_store.get(k))
    redis.set = AsyncMock(side_effect=lambda k, v, ex=None: flag_store.__setitem__(k, v))
    rec = {"status": "Open", "source": "dse_direct", "session_date": "2026-06-02",
           "checked_at": "2026-06-02T10:01:00+06:00"}
    with patch.object(sched, "refresh_market_status", AsyncMock(return_value=rec)), \
         patch.object(sched, "get_redis", AsyncMock(return_value=redis)), \
         patch.object(sched, "job_live_prices", AsyncMock()) as live:
        await sched.job_market_status_open()
        await sched.job_market_status_open()  # second fire — flag set, no re-trigger
    assert live.await_count == 1


@pytest.mark.asyncio
async def test_job_market_status_open_no_trigger_when_closed():
    rec = {"status": "Closed", "source": "dse_direct", "session_date": "2026-06-02",
           "checked_at": "2026-06-02T10:01:00+06:00"}
    redis = MagicMock(); redis.get = AsyncMock(return_value=None); redis.set = AsyncMock()
    with patch.object(sched, "refresh_market_status", AsyncMock(return_value=rec)), \
         patch.object(sched, "get_redis", AsyncMock(return_value=redis)), \
         patch.object(sched, "job_live_prices", AsyncMock()) as live:
        await sched.job_market_status_open()
    assert live.await_count == 0


@pytest.mark.asyncio
async def test_job_market_status_close_short_circuits_after_closed():
    rec = {"status": "Closed", "source": "dse_direct", "session_date": "2026-06-02",
           "checked_at": "2026-06-02T14:31:00+06:00"}
    flag_store = {}
    redis = MagicMock()
    redis.get = AsyncMock(side_effect=lambda k: flag_store.get(k))
    redis.set = AsyncMock(side_effect=lambda k, v, ex=None: flag_store.__setitem__(k, v))
    refresh = AsyncMock(return_value=rec)
    with patch.object(sched, "refresh_market_status", refresh), \
         patch.object(sched, "get_redis", AsyncMock(return_value=redis)):
        await sched.job_market_status_close()  # scrapes, sets flag
        await sched.job_market_status_close()  # flag set → short-circuit, no scrape
    assert refresh.await_count == 1
