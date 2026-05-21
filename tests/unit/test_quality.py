"""Unit tests for extraction/quality.py."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from extraction.quality import run_quality_checks


def _live_prices_df(**overrides):
    base = {
        "ticker":     ["GP", "BRACBANK"],
        "close":      [50.0, 30.0],
        "prev_close": [49.0, 29.0],
        "volume":     [1000, 2000],
        "fetched_at": [datetime.now(timezone.utc)] * 2,
    }
    base.update(overrides)
    return pd.DataFrame(base)


class TestRunQualityChecks:
    def test_clean_data_no_failures(self):
        df = _live_prices_df()
        failures = run_quality_checks(df, "live_prices")
        assert failures == []

    def test_empty_dataframe(self):
        failures = run_quality_checks(pd.DataFrame(), "live_prices")
        assert any(f.rule == "empty_result" for f in failures)

    def test_missing_column(self):
        df = _live_prices_df()
        df = df.drop(columns=["close"])
        failures = run_quality_checks(df, "live_prices")
        assert any(f.rule == "missing_columns" for f in failures)

    def test_price_spike_triggers_warning(self):
        df = _live_prices_df(
            close=[50.0, 100.0],
            prev_close=[49.0, 30.0],  # 100 → 30 prev = 233% spike
        )
        failures = run_quality_checks(df, "live_prices", price_spike_threshold=25.0)
        spike_failures = [f for f in failures if f.rule == "price_spike"]
        assert len(spike_failures) >= 1

    def test_no_spike_below_threshold(self):
        df = _live_prices_df(
            close=[50.0, 35.0],
            prev_close=[49.0, 30.0],  # 16.7% change — under 25%
        )
        failures = run_quality_checks(df, "live_prices", price_spike_threshold=25.0)
        assert not any(f.rule == "price_spike" for f in failures)

    def test_negative_price(self):
        df = _live_prices_df(close=[-1.0, 30.0])
        failures = run_quality_checks(df, "live_prices")
        assert any(f.rule == "negative_price" for f in failures)

    def test_unknown_stream_no_required_columns(self):
        df = pd.DataFrame({"x": [1]})
        failures = run_quality_checks(df, "unknown_stream")
        assert failures == []
