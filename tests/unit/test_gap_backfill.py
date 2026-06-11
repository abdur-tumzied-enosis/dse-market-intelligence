"""Tests for daily-bar gap backfill: detection, row building, orchestrator."""
from __future__ import annotations

from mgmt.config import get_settings


def test_gap_backfill_settings_defaults():
    cfg = get_settings()
    assert cfg.gap_backfill_enabled is True
    assert cfg.gap_backfill_window_days == 30
    assert cfg.gap_backfill_hour == 18
    assert cfg.gap_backfill_minute == 0
