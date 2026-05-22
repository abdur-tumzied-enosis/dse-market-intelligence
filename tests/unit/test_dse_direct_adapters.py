"""
Unit tests for DSE Direct adapters — offline, uses saved fixtures.
No network calls made.

Fixtures saved by: tests/smoke/test_dse_direct_smoke.py
Note: live_prices_df / announcements_df / depth_gp_df are pre-normalized
DataFrames (saved from result.data). Synthetic tests cover normalize()
methods directly.
"""
from __future__ import annotations

import pickle
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"


def _load_fixture(name: str):
    path = FIXTURE_DIR / f"dse_direct_{name}.pkl"
    if not path.exists():
        pytest.skip(f"fixture not found: {path.name} — run smoke tests first")
    with open(path, "rb") as f:
        return pickle.load(f)


# ---------------------------------------------------------------------------
# DSEDirectLivePricesAdapter — synthetic normalize() tests
# ---------------------------------------------------------------------------

class TestDSEDirectLivePricesNormalize:
    """Tests normalize() directly with a synthetic raw DataFrame."""

    @pytest.fixture(scope="class")
    def df(self):
        from extraction.adapters.dse_direct.live_prices import DSEDirectLivePricesAdapter
        # Columns mirror output of _parse_html() — headers already uppercased
        raw = pd.DataFrame([
            {
                "#": "1", "TRADING CODE": "gp", "LTP*": "250.50",
                "HIGH": "255.00", "LOW": "249.00", "CLOSEP*": "250.50",
                "YCP*": "248.00", "CHANGE": "2.50", "TRADE": "1,234",
                "VALUE (MN)": "123.45", "VOLUME": "456,789",
            },
            {
                "#": "2", "TRADING CODE": "BRACBANK", "LTP*": "32.10",
                "HIGH": "32.80", "LOW": "31.90", "CLOSEP*": "32.10",
                "YCP*": "31.80", "CHANGE": "0.30", "TRADE": "567",
                "VALUE (MN)": "18.20", "VOLUME": "567,890",
            },
        ])
        return DSEDirectLivePricesAdapter().normalize(raw)

    def test_two_rows(self, df: pd.DataFrame):
        assert len(df) == 2

    def test_ticker_uppercase(self, df: pd.DataFrame):
        assert df.iloc[0]["ticker"] == "GP"
        assert df.iloc[1]["ticker"] == "BRACBANK"

    def test_close_decimal(self, df: pd.DataFrame):
        assert isinstance(df.iloc[0]["close"], Decimal)
        assert df.iloc[0]["close"] == Decimal("250.50")

    def test_high_low_decimal(self, df: pd.DataFrame):
        assert isinstance(df.iloc[0]["high"], Decimal)
        assert isinstance(df.iloc[0]["low"], Decimal)
        assert df.iloc[0]["high"] >= df.iloc[0]["low"]

    def test_open_is_none(self, df: pd.DataFrame):
        # open not in DSE live feed
        assert df["open"].isna().all()

    def test_change_pct_is_none(self, df: pd.DataFrame):
        # change_pct not in DSE live feed
        assert df["change_pct"].isna().all()

    def test_source(self, df: pd.DataFrame):
        assert (df["source"] == "dse_direct_live_prices").all()

    def test_volume_strips_commas(self, df: pd.DataFrame):
        assert df.iloc[0]["volume"] == 456789
        assert df.iloc[1]["volume"] == 567890

    def test_fetched_at_utc(self, df: pd.DataFrame):
        assert df.iloc[0]["fetched_at"].tzinfo is not None


# ---------------------------------------------------------------------------
# DSEDirectLivePricesAdapter — fixture-based tests (pre-normalized)
# ---------------------------------------------------------------------------

class TestDSEDirectLivePricesFixture:
    @pytest.fixture(scope="class")
    def df(self):
        return _load_fixture("live_prices_df")

    def test_row_count(self, df: pd.DataFrame):
        assert len(df) > 50

    def test_required_columns(self, df: pd.DataFrame):
        required = {
            "ticker", "close", "high", "low", "prev_close", "change",
            "volume", "trades", "open", "change_pct", "fetched_at", "source",
        }
        missing = required - set(df.columns)
        assert not missing, f"missing columns: {missing}"

    def test_tickers_uppercase(self, df: pd.DataFrame):
        assert (df["ticker"] == df["ticker"].str.upper()).all()

    def test_source(self, df: pd.DataFrame):
        assert (df["source"] == "dse_direct_live_prices").all()

    def test_open_none(self, df: pd.DataFrame):
        assert df["open"].isna().all()

    def test_change_pct_none(self, df: pd.DataFrame):
        assert df["change_pct"].isna().all()

    def test_close_decimal(self, df: pd.DataFrame):
        val = df["close"].dropna().iloc[0]
        assert isinstance(val, Decimal)
        assert val > 0

    def test_fetched_at_utc(self, df: pd.DataFrame):
        assert df["fetched_at"].iloc[0].tzinfo is not None

    def test_no_empty_tickers(self, df: pd.DataFrame):
        assert (df["ticker"].str.len() > 0).all()


# ---------------------------------------------------------------------------
# DSEDirectAnnouncementsAdapter — synthetic normalize() tests
# ---------------------------------------------------------------------------

class TestDSEDirectAnnouncementsNormalize:
    """Tests normalize() with synthetic rows (same format _playwright_fetch_news returns)."""

    @pytest.fixture(scope="class")
    def df(self):
        from extraction.adapters.dse_direct.announcements import DSEDirectAnnouncementsAdapter
        rows: list[dict[str, Any]] = [
            {
                "ticker": "bracbank",
                "date_str": "2026-05-21",
                "headline": "AGM Notice",
                "details": "Annual General Meeting on 30 June 2026.",
                "category": "",
            },
            {
                "ticker": "GP",
                "date_str": "2026-05-20",
                "headline": "Dividend Announcement",
                "details": "Cash dividend 50%.",
                "category": "",
            },
        ]
        return DSEDirectAnnouncementsAdapter().normalize(rows)

    def test_two_rows(self, df: pd.DataFrame):
        assert len(df) == 2

    def test_ticker_uppercase(self, df: pd.DataFrame):
        assert df.iloc[0]["ticker"] == "BRACBANK"
        assert df.iloc[1]["ticker"] == "GP"

    def test_required_columns(self, df: pd.DataFrame):
        assert {"ticker", "published_at", "category", "headline", "details", "source"} <= set(df.columns)

    def test_source(self, df: pd.DataFrame):
        assert (df["source"] == "dse_direct_announcements").all()

    def test_published_at_utc(self, df: pd.DataFrame):
        ts = df.iloc[0]["published_at"]
        assert isinstance(ts, datetime)
        assert ts.tzinfo is not None

    def test_headline_preserved(self, df: pd.DataFrame):
        assert df.iloc[0]["headline"] == "AGM Notice"


# ---------------------------------------------------------------------------
# DSEDirectAnnouncementsAdapter — fixture-based tests (pre-normalized)
# ---------------------------------------------------------------------------

class TestDSEDirectAnnouncementsFixture:
    @pytest.fixture(scope="class")
    def df(self):
        return _load_fixture("announcements_df")

    def test_not_empty(self, df: pd.DataFrame):
        assert len(df) > 0

    def test_required_columns(self, df: pd.DataFrame):
        assert {"ticker", "published_at", "headline", "details", "source"} <= set(df.columns)

    def test_source(self, df: pd.DataFrame):
        assert (df["source"] == "dse_direct_announcements").all()

    def test_tickers_uppercase(self, df: pd.DataFrame):
        nonempty = df[df["ticker"].str.len() > 0]
        if len(nonempty) > 0:
            assert (nonempty["ticker"] == nonempty["ticker"].str.upper()).all()

    def test_published_at_utc(self, df: pd.DataFrame):
        ts = df["published_at"].dropna().iloc[0]
        assert isinstance(ts, datetime)
        assert ts.tzinfo is not None


# ---------------------------------------------------------------------------
# DSEDirectDepthPlaywrightAdapter — synthetic normalize_price_stats() tests
# ---------------------------------------------------------------------------

class TestDSEDirectDepthNormalize:
    @pytest.fixture(scope="class")
    def df(self):
        from extraction.adapters.dse_direct.depth import DSEDirectDepthPlaywrightAdapter
        adapter = DSEDirectDepthPlaywrightAdapter()
        stats = {
            "Open Price":            ": 239.8",
            "Last Trade Price":      ": 241.0",
            "Yesterday Close Price": ": 238.5",
            "No. of Trade":          "1234",
        }
        return adapter.normalize_price_stats(stats, ticker="gp")

    def test_single_row(self, df: pd.DataFrame):
        assert len(df) == 1

    def test_ticker_uppercase(self, df: pd.DataFrame):
        assert df.iloc[0]["ticker"] == "GP"

    def test_open_decimal(self, df: pd.DataFrame):
        assert isinstance(df.iloc[0]["open"], Decimal)
        assert df.iloc[0]["open"] == Decimal("239.8")

    def test_close_decimal(self, df: pd.DataFrame):
        assert isinstance(df.iloc[0]["close"], Decimal)
        assert df.iloc[0]["close"] == Decimal("241.0")

    def test_prev_close_decimal(self, df: pd.DataFrame):
        assert isinstance(df.iloc[0]["prev_close"], Decimal)
        assert df.iloc[0]["prev_close"] == Decimal("238.5")

    def test_auth_required_true(self, df: pd.DataFrame):
        assert df.iloc[0]["auth_required"]

    def test_bid_ask_none(self, df: pd.DataFrame):
        # bid/ask unavailable without DSE auth
        assert df.iloc[0]["bid_price_1"] is None
        assert df.iloc[0]["ask_price_1"] is None

    def test_source(self, df: pd.DataFrame):
        assert df.iloc[0]["source"] == "dse_direct_depth"

    def test_fetched_at_utc(self, df: pd.DataFrame):
        assert df.iloc[0]["fetched_at"].tzinfo is not None

    def test_bid_ask_columns_present(self, df: pd.DataFrame):
        for i in range(1, 6):
            assert f"bid_price_{i}" in df.columns
            assert f"ask_price_{i}" in df.columns


# ---------------------------------------------------------------------------
# DSEDirectDepthPlaywrightAdapter — fixture-based tests (pre-normalized)
# ---------------------------------------------------------------------------

class TestDSEDirectDepthFixture:
    @pytest.fixture(scope="class")
    def df(self):
        return _load_fixture("depth_gp_df")

    def test_single_row(self, df: pd.DataFrame):
        assert len(df) == 1

    def test_ticker_gp(self, df: pd.DataFrame):
        assert df.iloc[0]["ticker"] == "GP"

    def test_auth_required(self, df: pd.DataFrame):
        assert df.iloc[0]["auth_required"]

    def test_source(self, df: pd.DataFrame):
        assert df.iloc[0]["source"] == "dse_direct_depth"

    def test_bid_ask_none_without_auth(self, df: pd.DataFrame):
        assert df.iloc[0]["bid_price_1"] is None
        assert df.iloc[0]["ask_price_1"] is None

    def test_fetched_at_utc(self, df: pd.DataFrame):
        assert df.iloc[0]["fetched_at"].tzinfo is not None
