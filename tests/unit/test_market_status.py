import os
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from datetime import datetime

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
