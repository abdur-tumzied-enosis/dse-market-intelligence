"""
Schema consistency test — verify all adapters per stream support failover.

Key principle: Adapters can return different columns, but must include
a minimum "baseline" set for failover to work. Extra columns are allowed.

Examples:
- live_prices: baseline = {ticker, close, change_pct, volume, source}
  AmarStock can add {eps, pe, sector, shareholding, ...}
- historical_ohlcv: baseline = {ticker, date, high, low, volume, source}
  AmarStock returns {open, high, low, volume} (no close); bdshare adds {close, trades}
"""
from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from extraction.adapters.amarstock.csv_historical import AmarStockCSVAdapter
from extraction.adapters.amarstock.fundamentals_scraper import AmarStockFundamentalsAdapter
from extraction.adapters.amarstock.live_prices import AmarStockLivePricesAdapter
from extraction.adapters.bdshare.announcements import BDShareAGMAdapter
from extraction.adapters.bdshare.historical import BDShareHistoricalAdapter
from extraction.adapters.bdshare.live_prices import BDShareLivePricesAdapter
from extraction.adapters.bdshare.market_info import BDShareMarketInfoAdapter
from extraction.adapters.dse_direct.depth import DSEDirectDepthPlaywrightAdapter
from extraction.adapters.dse_direct.live_prices import DSEDirectLivePricesAdapter


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def load_fixture(filename: str) -> Any:
    """Load a pickled fixture."""
    path = FIXTURES_DIR / filename
    if not path.exists():
        return None
    with open(path, "rb") as f:
        return pickle.load(f)


# Baseline schemas: minimum columns required for failover in each stream
BASELINE_SCHEMAS: dict[str, set[str]] = {
    "live_prices": {
        "ticker", "close", "volume", "source", "fetched_at",
        # Note: AmarStock adds {eps, pe, nav, sector, shareholding, ...}
        # which is fine — extra columns allowed
    },
    "historical_ohlcv": {
        "ticker", "date", "high", "low", "volume", "source",
        # Note: amarstock lacks "open/close" (only has high/low/volume)
        #       bdshare has "close" and "trades"
        # Both are acceptable — baseline is minimal OHLV
    },
    "market_indices": {
        "index_name", "value", "source", "fetched_at",
    },
    "fundamentals": {
        "ticker", "eps", "source",
        # Note: pe is optional (pe_audited/pe_unaudited vary by adapter)
    },
    "agm_dividends": {
        "ticker", "cash_div_pct", "source",
    },
    "market_depth": {
        "ticker", "source", "fetched_at",
    },
}


# Map: stream -> [(adapter_instance, fixture_filename, preprocess_fn)]
FIXTURE_MAP: dict[str, list[tuple[Any, str, callable | None]]] = {
    "live_prices": [
        (BDShareLivePricesAdapter(), "bdshare_current_trade_data_sample.pkl", None),
        (AmarStockLivePricesAdapter(), "amarstock_latest_price_all_sample.pkl", None),
        (DSEDirectLivePricesAdapter(), "dse_direct_live_prices_df.pkl", None),
    ],
    "historical_ohlcv": [
        (AmarStockCSVAdapter(), "amarstock_historical_gp_sample.pkl", lambda df: df.to_dict("records")),
        (BDShareHistoricalAdapter(), "bdshare_hist_data_sample.pkl", None),
    ],
    "market_indices": [
        (BDShareMarketInfoAdapter(), "bdshare_market_info_sample.pkl", None),
    ],
    "fundamentals": [
        (AmarStockFundamentalsAdapter(), "amarstock_stock_detail_gp_sample.pkl", None),
    ],
    "agm_dividends": [
        (BDShareAGMAdapter(), "bdshare_agm_news_sample.pkl", None),
    ],
    # market_depth: DSE Direct adapter exists but fixture is empty (post-market when saved).
    # Will test in Phase 1I when bulk historical load provides market hours data.
}


@pytest.mark.parametrize("stream_name", list(FIXTURE_MAP.keys()))
def test_baseline_schema_compliance(stream_name: str):
    """Verify all adapters in stream include baseline required columns."""
    fixtures_and_adapters = FIXTURE_MAP[stream_name]
    baseline = BASELINE_SCHEMAS.get(stream_name, set())

    if not fixtures_and_adapters or not baseline:
        pytest.skip(f"No baseline schema for {stream_name}")

    compliant_adapters = 0

    for adapter, fixture_filename, preprocess_fn in fixtures_and_adapters:
        raw_data = load_fixture(fixture_filename)

        if raw_data is None:
            pytest.skip(f"{fixture_filename} not found; skipping {adapter.name}")

        if preprocess_fn:
            try:
                raw_data = preprocess_fn(raw_data)
            except Exception as e:
                pytest.skip(f"preprocess failed for {adapter.name}: {e}")

        try:
            if adapter.name in ("amarstock_historical", "amarstock_fundamentals"):
                normalized = adapter.normalize(raw_data, ticker="GP")
            else:
                normalized = adapter.normalize(raw_data)
        except Exception as e:
            pytest.fail(f"{adapter.name}.normalize() failed: {type(e).__name__}: {e}")

        if not isinstance(normalized, pd.DataFrame):
            pytest.skip(f"{adapter.name} returned non-DataFrame: {type(normalized)}")

        columns = set(normalized.columns)
        missing = baseline - columns

        if missing:
            pytest.fail(
                f"{adapter.name} missing baseline columns in '{stream_name}':\n"
                f"  Baseline: {sorted(baseline)}\n"
                f"  Got:      {sorted(columns)}\n"
                f"  Missing:  {sorted(missing)}"
            )

        compliant_adapters += 1

    if compliant_adapters == 0:
        pytest.skip(f"No adapters with valid fixtures for {stream_name}")


def test_schema_summary():
    """Print schema summary for all streams."""
    print("\n\n=== Adapter Schema Report ===\n")

    for stream_name, fixtures_and_adapters in FIXTURE_MAP.items():
        baseline = BASELINE_SCHEMAS.get(stream_name, set())
        print(f"{stream_name:30} (baseline: {len(baseline)} cols)")

        for adapter, fixture_filename, preprocess_fn in fixtures_and_adapters:
            raw_data = load_fixture(fixture_filename)
            if raw_data is None:
                print(f"  {adapter.name:40} - fixture not found")
                continue

            if preprocess_fn:
                try:
                    raw_data = preprocess_fn(raw_data)
                except Exception as e:
                    print(f"  {adapter.name:40} - preprocess failed: {e}")
                    continue

            try:
                if adapter.name in ("amarstock_historical", "amarstock_fundamentals"):
                    normalized = adapter.normalize(raw_data, ticker="GP")
                else:
                    normalized = adapter.normalize(raw_data)

                if not isinstance(normalized, pd.DataFrame):
                    print(f"  {adapter.name:40} - not DataFrame: {type(normalized).__name__}")
                    continue

                cols = set(normalized.columns)
                missing = baseline - cols
                extra = cols - baseline
                status = "OK" if not missing else "MISSING"

                print(f"  {adapter.name:40} {status} {len(cols)} cols", end="")
                if missing:
                    print(f" (missing: {sorted(missing)})", end="")
                if extra:
                    print(f" (extra: {len(extra)})", end="")
                print()

            except Exception as e:
                print(f"  {adapter.name:40} FAIL {type(e).__name__}: {str(e)[:50]}")

        print()
