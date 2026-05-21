"""
Unit tests for AmarStock adapters — offline, uses saved fixtures.
No network calls made.

Fixtures saved by: tests/smoke/test_amarstock_smoke.py
"""
from __future__ import annotations

import pickle
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"


def _load(name: str):
    path = FIXTURE_DIR / f"amarstock_{name}_sample.pkl"
    if not path.exists():
        pytest.skip(f"fixture not found: {path.name} — run smoke tests first")
    with open(path, "rb") as f:
        return pickle.load(f)


# ---------------------------------------------------------------------------
# AmarStockLivePricesAdapter
# ---------------------------------------------------------------------------

class TestLivePricesNormalize:
    @pytest.fixture(scope="class")
    def df(self):
        from extraction.adapters.amarstock.live_prices import AmarStockLivePricesAdapter
        raw = _load("latest_price_all")
        return AmarStockLivePricesAdapter().normalize(raw)

    def test_row_count(self, df: pd.DataFrame):
        assert len(df) == 426

    def test_required_columns(self, df: pd.DataFrame):
        required = {
            "ticker", "open", "high", "low", "close", "ltp", "prev_close",
            "change", "change_pct", "volume", "trades",
            "eps", "nav", "pe", "free_float",
            "sponsor_pct", "institution_pct", "foreign_pct", "public_pct",
            "sector", "full_name", "fetched_at", "source",
        }
        missing = required - set(df.columns)
        assert not missing, f"missing columns: {missing}"

    def test_source_value(self, df: pd.DataFrame):
        assert (df["source"] == "amarstock_live_prices").all()

    def test_fetched_at_is_utc(self, df: pd.DataFrame):
        ts = df["fetched_at"].iloc[0]
        assert ts.tzinfo is not None

    def test_squrpharma_row(self, df: pd.DataFrame):
        row = df[df["ticker"] == "SQURPHARMA"]
        assert len(row) == 1
        r = row.iloc[0]
        assert r["full_name"] == "Square Pharmaceuticals PLC."
        assert r["sector"] == "Pharmaceuticals and Chemicals"
        assert isinstance(r["close"], Decimal)
        assert r["close"] > 0

    def test_decimal_numeric_fields(self, df: pd.DataFrame):
        row = df[df["ticker"] == "GP"].iloc[0]
        for field in ("open", "high", "low", "close", "eps", "nav"):
            val = row[field]
            assert val is None or isinstance(val, Decimal), (
                f"{field} should be Decimal or None, got {type(val)}"
            )

    def test_integer_volume(self, df: pd.DataFrame):
        row = df[df["ticker"] == "GP"].iloc[0]
        import numpy as np
        assert isinstance(row["volume"], (int, np.integer))
        assert row["volume"] > 0

    def test_no_empty_tickers(self, df: pd.DataFrame):
        assert (df["ticker"].str.len() > 0).all()

    def test_shareholding_sum_approx_100(self, df: pd.DataFrame):
        row = df[df["ticker"] == "BRACBANK"].iloc[0]
        total = sum(
            float(row[f]) for f in ("sponsor_pct", "institution_pct", "foreign_pct", "public_pct")
            if row[f] is not None
        )
        assert 95 <= total <= 105, f"shareholding sum = {total}, expected ~100"


# ---------------------------------------------------------------------------
# AmarStockFundamentalsAdapter
# ---------------------------------------------------------------------------

class TestFundamentalsNormalize:
    @pytest.fixture(scope="class")
    def df(self):
        from extraction.adapters.amarstock.fundamentals_scraper import AmarStockFundamentalsAdapter
        raw = _load("stock_detail_squrpharma")
        return AmarStockFundamentalsAdapter().normalize(raw, ticker="SQURPHARMA")

    def test_single_row(self, df: pd.DataFrame):
        assert len(df) == 1

    def test_ticker(self, df: pd.DataFrame):
        assert df.iloc[0]["ticker"] == "SQURPHARMA"

    def test_source(self, df: pd.DataFrame):
        assert df.iloc[0]["source"] == "amarstock_fundamentals"

    def test_eps_decimal(self, df: pd.DataFrame):
        eps = df.iloc[0]["eps"]
        assert isinstance(eps, Decimal)
        assert eps == Decimal("27.04")

    def test_pe_fields(self, df: pd.DataFrame):
        r = df.iloc[0]
        assert r["pe_audited"] == Decimal("7.67")
        assert r["pe_unaudited"] == Decimal("6.68")

    def test_nav(self, df: pd.DataFrame):
        assert df.iloc[0]["nav"] == Decimal("157.88")

    def test_quarterly_eps(self, df: pd.DataFrame):
        r = df.iloc[0]
        assert r["eps_q1"] == Decimal("8.35")
        assert r["eps_q2"] == Decimal("8.2")
        assert r["eps_q3"] == Decimal("6.73")

    def test_shareholding_latest(self, df: pd.DataFrame):
        r = df.iloc[0]
        assert r["sponsor_pct"] == Decimal("44.15")
        assert r["institution_pct"] == Decimal("13.73")
        assert r["shareholding_date"] == "Apr 30, 2026"

    def test_shareholding_prior_periods(self, df: pd.DataFrame):
        r = df.iloc[0]
        # 2nd period
        assert r["sponsor_pct_1"] == Decimal("44.15")
        # 3rd period (year-end)
        assert r["sponsor_pct_2"] == Decimal("43.59")

    def test_news1_date_utc(self, df: pd.DataFrame):
        news_date = df.iloc[0]["news1_date"]
        assert isinstance(news_date, datetime)
        assert news_date.tzinfo is not None

    def test_news1_title(self, df: pd.DataFrame):
        assert df.iloc[0]["news1_title"] == "Q3 Financials"

    def test_technical_signals(self, df: pd.DataFrame):
        r = df.iloc[0]
        assert r["ma10"] in ("Bullish", "Bearish")
        assert r["ma50"] in ("Bullish", "Bearish")
        assert r["beta"] is not None

    def test_company_info(self, df: pd.DataFrame):
        r = df.iloc[0]
        assert r["listing_year"] == 1995
        assert "squarepharma" in str(r["web"]).lower()
        assert "@" in str(r["email"])

    def test_balance_sheet_fields(self, df: pd.DataFrame):
        r = df.iloc[0]
        assert r["paid_up_cap_mn"] is not None
        assert r["reserve_surplus_mn"] is not None
        assert r["total_securities"] == 886451010

    @pytest.mark.parametrize("ticker", ["bracbank", "gp"])
    def test_other_tickers(self, ticker: str):
        from extraction.adapters.amarstock.fundamentals_scraper import AmarStockFundamentalsAdapter
        raw = _load(f"stock_detail_{ticker}")
        df = AmarStockFundamentalsAdapter().normalize(raw, ticker=ticker.upper())
        assert len(df) == 1
        assert df.iloc[0]["ticker"] == ticker.upper()
        assert df.iloc[0]["eps"] is not None


# ---------------------------------------------------------------------------
# AmarStockCSVAdapter (historical quotes)
# ---------------------------------------------------------------------------

class TestHistoricalNormalize:
    @pytest.fixture(scope="class")
    def df(self):
        from extraction.adapters.amarstock.csv_historical import AmarStockCSVAdapter
        raw_df: pd.DataFrame = _load("historical_gp")
        raw = raw_df.to_dict("records")
        return AmarStockCSVAdapter().normalize(raw, ticker="GP")

    def test_row_count(self, df: pd.DataFrame):
        assert len(df) == 628

    def test_required_columns(self, df: pd.DataFrame):
        assert {"ticker", "date", "high", "low", "volume", "trades", "value_bdt", "source"} <= set(df.columns)

    def test_ticker_value(self, df: pd.DataFrame):
        assert (df["ticker"] == "GP").all()

    def test_source_value(self, df: pd.DataFrame):
        assert (df["source"] == "amarstock_historical").all()

    def test_sorted_by_date_ascending(self, df: pd.DataFrame):
        dates = df["date"].dropna().tolist()
        assert dates == sorted(dates)

    def test_date_is_utc(self, df: pd.DataFrame):
        first = df["date"].iloc[0]
        assert isinstance(first, datetime)
        assert first.tzinfo is not None

    def test_high_low_decimal(self, df: pd.DataFrame):
        row = df.iloc[0]
        assert isinstance(row["high"], Decimal)
        assert isinstance(row["low"], Decimal)
        assert row["high"] >= row["low"]

    def test_no_null_dates(self, df: pd.DataFrame):
        assert df["date"].notna().all()

    def test_volume_positive(self, df: pd.DataFrame):
        assert (df["volume"] > 0).all()

    def test_date_range(self, df: pd.DataFrame):
        oldest = df["date"].min()
        newest = df["date"].max()
        assert oldest.year <= 2020
        assert newest.year >= 2025

    @pytest.mark.parametrize("ticker_name", ["squrpharma", "bracbank"])
    def test_other_tickers(self, ticker_name: str):
        from extraction.adapters.amarstock.csv_historical import AmarStockCSVAdapter
        raw_df: pd.DataFrame = _load(f"historical_{ticker_name}")
        raw = raw_df.to_dict("records")
        df = AmarStockCSVAdapter().normalize(raw, ticker=ticker_name.upper())
        assert len(df) > 10
        assert (df["ticker"] == ticker_name.upper()).all()
        assert "high" in df.columns
