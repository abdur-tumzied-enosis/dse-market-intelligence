"""Tests for crash/downtime recovery: reconciler + gap detection."""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytz

from mgmt.config import get_settings

BD_TZ = pytz.timezone("Asia/Dhaka")


def test_recovery_settings_defaults():
    cfg = get_settings()
    assert cfg.recovery_enabled is True
    assert cfg.intraday_gap_threshold_minutes == 3
