"""
Unit tests for BDShare adapters — offline, uses saved fixtures.
No network calls made.

Fixtures saved by: tests/smoke/test_bdshare_smoke.py
Run smoke tests first if fixtures are missing.
"""
from __future__ import annotations

import pickle
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"


def _load(name: str):
    path = FIXTURE_DIR / f"bdshare_{name}_sample.pkl"
    if not path.exists():
        pytest.skip(f"fixture not found: {path.name} — run smoke tests first")
    with open(path, "rb") as f:
        return pickle.load(f)


# ---------------------------------------------------------------------------
# BDShareLivePricesAdapter
# ---------------------------------------------------------------------------

class TestBDShareLivePricesNormalize:
    @pytest.fixture(scope="class")
    def df(self):
        from extraction.adapters.bdshare.live_prices import BDShareLivePricesAdapter
        raw = _load("current_trade_data")
        return BDShareLivePricesAdapter().normalize(raw)

    def test_row_count(self, df: pd.DataFrame):
        assert len(df) > 100

    def test_required_columns(self, df: pd.DataFrame):
        required = {
            "ticker", "open", "high", "low", "close", "prev_close",
            "change_pct", "volume", "trades", "value_bdt", "fetched_at", "source",
        }
        missing = required - set(df.columns)
        assert not missing, f"missing columns: {missing}"

    def test_open_is_none(self, df: pd.DataFrame):
        # bdshare live feed has no open price
        assert df["open"].isna().all()

    def test_source(self, df: pd.DataFrame):
        assert (df["source"] == "bdshare_live_prices").all()

    def test_close_decimal(self, df: pd.DataFrame):
        val = df["close"].dropna().iloc[0]
        assert isinstance(val, Decimal)
        assert val > 0

    def test_prev_close_decimal(self, df: pd.DataFrame):
        val = df["prev_close"].dropna().iloc[0]
        assert isinstance(val, Decimal)
        assert val > 0

    def test_high_low_decimal(self, df: pd.DataFrame):
        valid = df[df["high"].notna() & df["low"].notna()]
        assert len(valid) > 0
        assert isinstance(valid["high"].iloc[0], Decimal)
        assert isinstance(valid["low"].iloc[0], Decimal)

    def test_fetched_at_utc(self, df: pd.DataFrame):
        ts = df["fetched_at"].iloc[0]
        assert ts.tzinfo is not None

    def test_no_empty_tickers(self, df: pd.DataFrame):
        assert (df["ticker"].str.len() > 0).all()

    def test_tickers_uppercase(self, df: pd.DataFrame):
        assert (df["ticker"] == df["ticker"].str.upper()).all()

    def test_volume_numeric(self, df: pd.DataFrame):
        assert pd.to_numeric(df["volume"], errors="coerce").notna().any()


# ---------------------------------------------------------------------------
# BDShareHistoricalAdapter
# ---------------------------------------------------------------------------

class TestBDShareHistoricalNormalize:
    @pytest.fixture(scope="class")
    def df(self):
        from extraction.adapters.bdshare.historical import BDShareHistoricalAdapter
        raw = _load("hist_data")
        return BDShareHistoricalAdapter().normalize(raw)

    def test_required_columns(self, df: pd.DataFrame):
        assert {"ticker", "date", "open", "high", "low", "close", "volume", "source"} <= set(df.columns)

    def test_ticker_squrpharma(self, df: pd.DataFrame):
        assert (df["ticker"] == "SQURPHARMA").all()

    def test_source(self, df: pd.DataFrame):
        assert (df["source"] == "bdshare_historical").all()

    def test_row_count(self, df: pd.DataFrame):
        # fixture is SQURPHARMA 2024 — ~240 trading days
        assert len(df) > 50

    def test_dates_utc(self, df: pd.DataFrame):
        first = df["date"].iloc[0]
        assert isinstance(first, datetime)
        assert first.tzinfo is not None

    def test_no_null_dates(self, df: pd.DataFrame):
        assert df["date"].notna().all()

    def test_open_decimal(self, df: pd.DataFrame):
        val = df["open"].dropna().iloc[0]
        assert isinstance(val, Decimal)
        assert val > 0

    def test_close_decimal(self, df: pd.DataFrame):
        val = df["close"].dropna().iloc[0]
        assert isinstance(val, Decimal)
        assert val > 0

    def test_high_gte_low(self, df: pd.DataFrame):
        valid = df[df["high"].notna() & df["low"].notna()]
        assert (valid["high"] >= valid["low"]).all()

    def test_volume_non_negative(self, df: pd.DataFrame):
        numeric = pd.to_numeric(df["volume"], errors="coerce").dropna()
        assert (numeric >= 0).all()


# ---------------------------------------------------------------------------
# BDShareMarketInfoAdapter
# ---------------------------------------------------------------------------

class TestBDShareMarketInfoNormalize:
    @pytest.fixture(scope="class")
    def df(self):
        from extraction.adapters.bdshare.market_info import BDShareMarketInfoAdapter
        raw = _load("market_info")
        return BDShareMarketInfoAdapter().normalize(raw)

    def test_three_rows(self, df: pd.DataFrame):
        assert len(df) == 3

    def test_index_names(self, df: pd.DataFrame):
        assert set(df["index_name"].tolist()) == {"DSEX", "DS30", "DSES"}

    def test_required_columns(self, df: pd.DataFrame):
        assert {"index_name", "value", "change_pct", "fetched_at", "source"} <= set(df.columns)

    def test_value_decimal(self, df: pd.DataFrame):
        for val in df["value"].dropna():
            assert isinstance(val, Decimal), f"expected Decimal, got {type(val)}"

    def test_source(self, df: pd.DataFrame):
        assert (df["source"] == "bdshare_market_info").all()

    def test_change_pct_none(self, df: pd.DataFrame):
        # change_pct not available from bdshare market_info endpoint
        assert df["change_pct"].isna().all()

    def test_fetched_at_utc(self, df: pd.DataFrame):
        ts = df["fetched_at"].iloc[0]
        assert ts.tzinfo is not None

    def test_dsex_value_positive(self, df: pd.DataFrame):
        row = df[df["index_name"] == "DSEX"].iloc[0]
        assert row["value"] > 0


# ---------------------------------------------------------------------------
# BDShareAGMAdapter
# ---------------------------------------------------------------------------

class TestBDShareAGMNormalize:
    @pytest.fixture(scope="class")
    def df(self):
        from extraction.adapters.bdshare.announcements import BDShareAGMAdapter
        raw = _load("agm_news")
        return BDShareAGMAdapter().normalize(raw)

    def test_row_count(self, df: pd.DataFrame):
        # fixture captured ~209 rows
        assert len(df) > 100

    def test_required_columns(self, df: pd.DataFrame):
        assert {"ticker", "agm_date", "source"} <= set(df.columns)

    def test_source(self, df: pd.DataFrame):
        assert (df["source"] == "bdshare_agm").all()

    def test_agm_date_utc_or_none(self, df: pd.DataFrame):
        for val in df["agm_date"].dropna():
            assert isinstance(val, datetime), f"expected datetime, got {type(val)}"
            assert val.tzinfo is not None

    def test_agm_dates_not_all_none(self, df: pd.DataFrame):
        assert df["agm_date"].notna().any(), "expected some rows to have a parsed agm_date"

    def test_no_empty_tickers(self, df: pd.DataFrame):
        assert (df["ticker"].str.len() > 0).all()
