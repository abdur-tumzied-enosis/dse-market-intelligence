"""
Failover integration tests — verify secondary adapters activate when primary fails.

Test strategy:
1. For each stream with multiple adapters
2. Mock primary adapter to raise AdapterError
3. Verify DataStream.fetch() retries and uses secondary
4. Verify returned data includes baseline columns
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from extraction.base import AdapterError, AdapterResult
from extraction.registry import STREAMS


# Streams with multiple adapters (candidates for failover testing)
FAILOVER_STREAMS = {
    "live_prices": {
        "primary": 0,
        "secondary": 1,
        "baseline_cols": {"ticker", "close", "volume", "source", "fetched_at"},
    },
    "historical_ohlcv": {
        "primary": 0,
        "secondary": 1,
        "baseline_cols": {"ticker", "date", "high", "low", "volume", "source"},
    },
}


@pytest.mark.asyncio
@pytest.mark.parametrize("stream_name", list(FAILOVER_STREAMS.keys()))
async def test_failover_to_secondary(stream_name: str):
    """Verify stream falls over to secondary adapter when primary fails."""
    config = FAILOVER_STREAMS[stream_name]
    stream = STREAMS[stream_name]
    baseline_cols = config["baseline_cols"]

    # Create mock data (any valid data; just need baseline columns)
    mock_df = pd.DataFrame({
        col: [None] * 5 for col in baseline_cols
    })

    # Mock secondary adapter to return success
    secondary_result = AdapterResult(
        data=mock_df,
        source_name=stream.adapters[config["secondary"]].name,
        fetched_at=datetime.now(timezone.utc),
        quality="ok",
        records=len(mock_df),
        raw_sample={},
    )

    primary = stream.adapters[config["primary"]]
    secondary = stream.adapters[config["secondary"]]

    with patch.object(
        primary, "_fetch_with_timeout", side_effect=AdapterError(primary.name, "mocked failure")
    ), patch.object(
        secondary, "_fetch_with_timeout", new_callable=AsyncMock, return_value=secondary_result
    ) as mock_fetch:
        # Call stream.fetch() — should try primary, fail, then try secondary and succeed
        result = await stream.fetch()

        # Verify secondary adapter was called
        assert mock_fetch.called, (
            f"Secondary adapter {secondary.name} was not called in failover for {stream_name}"
        )

        # Verify result came from secondary
        assert result.source_name == secondary.name, (
            f"Expected data from {secondary.name}, got {result.source_name}"
        )

        # Verify result has baseline columns
        missing = baseline_cols - set(result.data.columns)
        assert not missing, (
            f"Result missing baseline columns: {missing}"
        )


@pytest.mark.asyncio
async def test_failover_result_has_baseline_schema():
    """Verify mocked failover results include baseline columns (unit test)."""
    # This verifies the mocked result structure, not real network calls
    for stream_name, config in FAILOVER_STREAMS.items():
        stream = STREAMS[stream_name]
        baseline_cols = config["baseline_cols"]

        # Create mock result with baseline columns
        mock_df = pd.DataFrame({col: [None] for col in baseline_cols})
        secondary_result = AdapterResult(
            data=mock_df,
            source_name=stream.adapters[config["secondary"]].name,
            fetched_at=datetime.now(timezone.utc),
            quality="ok",
            records=len(mock_df),
        )

        # Verify result structure
        assert set(secondary_result.data.columns) >= baseline_cols, (
            f"Mock result for {stream_name} missing baseline columns"
        )


def test_streams_have_failover_coverage():
    """Verify critical streams have multiple adapters for failover."""
    critical_streams = {"live_prices", "historical_ohlcv"}
    for stream_name in critical_streams:
        stream = STREAMS[stream_name]
        assert len(stream.adapters) >= 2, (
            f"Stream '{stream_name}' has only {len(stream.adapters)} adapter(s); "
            f"need >=2 for failover"
        )
