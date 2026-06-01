# tests/unit/test_market_regime.py
import os
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

import pytest

from api.routers.market import compute_regime


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
