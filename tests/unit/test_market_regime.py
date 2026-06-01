# tests/unit/test_market_regime.py
import os
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from api.routers.market import compute_regime, market_regime


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, *args, **kwargs):
        return self._rows


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        return False


class _FakePool:
    """Minimal asyncpg-pool stand-in: pool.acquire() yields a conn with fetch()."""

    def __init__(self, rows):
        self._conn = _FakeConn(rows)

    def acquire(self):
        return _FakeAcquire(self._conn)


def test_bull_when_dsex_at_or_above_ma():
    # most-recent-first; latest 5500 is above the mean of the series
    series = [5500.0] + [5000.0] * 49
    r = compute_regime(series, as_of="2026-06-01")
    assert r["regime"] == "Bull"
    assert r["window"] == 50
    assert r["provisional"] is False
    assert r["data_status"] == "ok"
    assert r["dsex"] == pytest.approx(5500.0)
    assert r["ma"] == pytest.approx((5500.0 + 5000.0 * 49) / 50)
    assert r["distance_pct"] > 0
    assert r["as_of"] == "2026-06-01"


def test_bull_on_exact_tie():
    # dsex exactly equals the MA → Bull (>= tie-break)
    series = [5000.0] * 50
    r = compute_regime(series, as_of="2026-06-01")
    assert r["regime"] == "Bull"
    assert r["dsex"] == pytest.approx(r["ma"])


def test_bear_when_dsex_below_ma():
    series = [4800.0] + [5000.0] * 49
    r = compute_regime(series, as_of="2026-06-01")
    assert r["regime"] == "Bear"
    assert r["distance_pct"] < 0
    assert r["data_status"] == "ok"


def test_provisional_when_window_below_50():
    series = [5100.0] + [5000.0] * 31  # 32 days
    r = compute_regime(series, as_of="2026-06-01")
    assert r["window"] == 32
    assert r["provisional"] is True
    assert r["data_status"] == "provisional"
    assert r["regime"] == "Bull"


def test_insufficient_when_fewer_than_five_days():
    series = [5000.0, 5010.0, 4990.0]  # 3 days
    r = compute_regime(series, as_of="2026-06-01")
    assert r["regime"] == "Unknown"
    assert r["data_status"] == "insufficient"
    assert r["ma"] is None
    assert r["window"] == 3
    assert r["dsex"] == pytest.approx(5000.0)


def test_empty_series():
    r = compute_regime([], as_of=None)
    assert r["regime"] == "Unknown"
    assert r["data_status"] == "insufficient"
    assert r["dsex"] is None
    assert r["ma"] is None
    assert r["window"] == 0
    assert r["as_of"] is None


# ── Endpoint tests: /market/regime reads index_daily and shapes the response ──


@pytest.mark.asyncio
async def test_regime_endpoint_shape_from_rows():
    """Endpoint loads DSEX rows (DESC), computes regime, returns the full shape."""
    # Only rows[0]'s date is asserted (as_of); filler dates just need to be valid.
    rows = [{"date": date(2026, 6, 1), "dsex": Decimal("5500")}] + [
        {"date": date(2026, 5, 1), "dsex": Decimal("5000")} for _ in range(49)
    ]
    pool = _FakePool(rows)
    with patch("api.routers.market._cache_get", AsyncMock(return_value=None)), \
         patch("api.routers.market._cache_set", AsyncMock()) as cset:
        result = await market_regime(pool=pool, _user=None)

    assert result["regime"] == "Bull"
    assert result["window"] == 50
    assert result["data_status"] == "ok"
    assert result["dsex"] == pytest.approx(5500.0)
    assert result["as_of"] == "2026-06-01"  # most-recent row's date, ISO
    # ok-status result is cached at the full TTL
    assert cset.await_args.kwargs["ttl"] == 300


@pytest.mark.asyncio
async def test_regime_endpoint_insufficient_when_empty():
    """Empty table → 200 with Unknown/insufficient, short-cached (not 502)."""
    pool = _FakePool([])
    with patch("api.routers.market._cache_get", AsyncMock(return_value=None)), \
         patch("api.routers.market._cache_set", AsyncMock()) as cset:
        result = await market_regime(pool=pool, _user=None)

    assert result["regime"] == "Unknown"
    assert result["data_status"] == "insufficient"
    assert result["dsex"] is None
    assert result["as_of"] is None
    assert cset.await_args.kwargs["ttl"] == 30  # short TTL while data is thin
