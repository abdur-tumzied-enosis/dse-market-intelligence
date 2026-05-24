"""Unit tests for BSEC adapters — offline, uses saved fixtures + synthetic data."""
from __future__ import annotations

import pickle
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"


def _load(name: str):
    path = FIXTURE_DIR / f"bsec_{name}.pkl"
    if not path.exists():
        pytest.skip(f"fixture not found: {path.name} — run smoke tests first")
    with open(path, "rb") as f:
        return pickle.load(f)


# ---------------------------------------------------------------------------
# _parse_date helper
# ---------------------------------------------------------------------------

class TestParseDate:
    @pytest.fixture(autouse=True)
    def _fn(self):
        from extraction.adapters.bsec.ipo_scraper import _parse_date
        self.fn = _parse_date

    def test_standard_format(self):
        assert self.fn("14 Jan, 2024") == "2024-01-14"

    def test_single_digit_day(self):
        assert self.fn("5 Mar, 2022") == "2022-03-05"

    def test_full_month_name(self):
        assert self.fn("14 January, 2024") == "2024-01-14"

    def test_empty_returns_none(self):
        assert self.fn("") is None

    def test_garbage_returns_none(self):
        assert self.fn("N/A") is None

    def test_date_with_surrounding_text(self):
        result = self.fn("Subscription Opens: 19 Jan, 2025")
        assert result == "2025-01-19"


# ---------------------------------------------------------------------------
# _parse_amount helper
# ---------------------------------------------------------------------------

class TestParseAmount:
    @pytest.fixture(autouse=True)
    def _fn(self):
        from extraction.adapters.bsec.ipo_scraper import _parse_amount
        self.fn = _parse_amount

    def test_integer(self):
        assert self.fn("5") == Decimal("5")

    def test_decimal(self):
        assert self.fn("12.50") == Decimal("12.50")

    def test_with_units(self):
        assert self.fn("30.00 Crore") == Decimal("30.00")

    def test_empty_returns_none(self):
        assert self.fn("") is None

    def test_returns_decimal_type(self):
        result = self.fn("5.00")
        assert isinstance(result, Decimal)


# ---------------------------------------------------------------------------
# BsecIPOAdapter normalize() — synthetic DataFrame
# ---------------------------------------------------------------------------

class TestBsecNormalize:
    @pytest.fixture(scope="class")
    def df(self):
        from extraction.adapters.bsec.ipo_scraper import BsecIPOAdapter
        raw = pd.DataFrame([
            {
                "company_name":   "Test Corp PLC",
                "ipo_type":       "fixed",
                "consent_date":   "2024-01-15",
                "sub_open_date":  "2024-02-01",
                "sub_close_date": "2024-02-05",
                "nrb_close_date": "2024-02-05",
                "amount_crore":   Decimal("30.00"),
                "prospectus_url": "https://sec.gov.bd/prospectus/test.pdf",
            },
            {
                "company_name":   "Bookbuild Ltd",
                "ipo_type":       "bookbuilding",
                "consent_date":   "2023-06-01",
                "sub_open_date":  "2023-07-01",
                "sub_close_date": "2023-07-05",
                "nrb_close_date": "2023-07-05",
                "amount_crore":   Decimal("150.00"),
                "prospectus_url": None,
            },
        ])
        return BsecIPOAdapter().normalize(raw)

    def test_two_rows(self, df: pd.DataFrame):
        assert len(df) == 2

    def test_required_columns(self, df: pd.DataFrame):
        required = {
            "company_name", "ipo_type", "consent_date", "sub_open_date",
            "sub_close_date", "amount_crore", "source", "fetched_at",
        }
        missing = required - set(df.columns)
        assert not missing, f"missing: {missing}"

    def test_source(self, df: pd.DataFrame):
        assert (df["source"] == "bsec_ipo").all()

    def test_fetched_at_utc(self, df: pd.DataFrame):
        assert df["fetched_at"].iloc[0].tzinfo is not None

    def test_ipo_types(self, df: pd.DataFrame):
        types = set(df["ipo_type"].unique())
        assert types == {"fixed", "bookbuilding"}

    def test_amount_decimal(self, df: pd.DataFrame):
        v = df.iloc[0]["amount_crore"]
        assert isinstance(v, Decimal)
        assert v == Decimal("30.00")


# ---------------------------------------------------------------------------
# BsecIPOAdapter — fixture-based tests
# ---------------------------------------------------------------------------

class TestBsecIPOFixture:
    @pytest.fixture(scope="class")
    def df(self):
        return _load("ipo_sample")

    def test_row_count(self, df: pd.DataFrame):
        # ~154 rows total (137 fixed + 17 bookbuilding as of 2026-05)
        assert len(df) >= 100

    def test_required_columns(self, df: pd.DataFrame):
        required = {
            "company_name", "ipo_type", "consent_date",
            "sub_open_date", "sub_close_date", "amount_crore",
            "source", "fetched_at",
        }
        missing = required - set(df.columns)
        assert not missing, f"missing: {missing}"

    def test_source(self, df: pd.DataFrame):
        assert (df["source"] == "bsec_ipo").all()

    def test_ipo_types_valid(self, df: pd.DataFrame):
        assert set(df["ipo_type"].unique()) <= {"fixed", "bookbuilding"}

    def test_fixed_and_bookbuilding_present(self, df: pd.DataFrame):
        assert "fixed" in df["ipo_type"].values
        assert "bookbuilding" in df["ipo_type"].values

    def test_company_names_not_empty(self, df: pd.DataFrame):
        assert (df["company_name"].str.len() > 0).all()

    def test_amount_decimal_or_none(self, df: pd.DataFrame):
        for v in df["amount_crore"].dropna():
            assert isinstance(v, Decimal), f"expected Decimal, got {type(v)}"

    def test_consent_dates_iso_format(self, df: pd.DataFrame):
        import re
        for d in df["consent_date"].dropna():
            assert re.match(r"^\d{4}-\d{2}-\d{2}$", str(d)), f"bad date: {d!r}"

    def test_fetched_at_utc(self, df: pd.DataFrame):
        assert df["fetched_at"].iloc[0].tzinfo is not None

    def test_has_old_ipos(self, df: pd.DataFrame):
        # Should have IPOs from before 2020
        years = df["consent_date"].dropna().apply(lambda d: str(d)[:4]).astype(int)
        assert (years < 2020).any()

    def test_prospectus_urls_are_https(self, df: pd.DataFrame):
        urls = df["prospectus_url"].dropna()
        assert (urls.str.startswith("https://")).all()
