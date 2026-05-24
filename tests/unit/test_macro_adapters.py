"""Unit tests for macro adapters — offline, uses saved fixtures + synthetic data."""
from __future__ import annotations

import pickle
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"


def _load(name: str):
    path = FIXTURE_DIR / f"macro_{name}.pkl"
    if not path.exists():
        pytest.skip(f"fixture not found: {path.name} — run smoke tests first")
    with open(path, "rb") as f:
        return pickle.load(f)


# ---------------------------------------------------------------------------
# _parse_period helper — pure unit tests, no fixtures
# ---------------------------------------------------------------------------

class TestParsePeriod:
    @pytest.fixture(autouse=True)
    def _fn(self):
        from extraction.adapters.macro.bangladesh_bank import _parse_period
        self.fn = _parse_period

    def test_iso_year_month(self):
        assert self.fn("2024-07") == "2024-07"

    def test_iso_with_day(self):
        assert self.fn("2024-07-15") == "2024-07"

    def test_dd_mm_yyyy_slash(self):
        assert self.fn("15/07/2024") == "2024-07"

    def test_dd_mm_yyyy_dash(self):
        assert self.fn("01-03-2023") == "2023-03"

    def test_month_year_full(self):
        assert self.fn("July 2024") == "2024-07"

    def test_month_year_abbr(self):
        assert self.fn("Jul-24") == "2024-07"

    def test_fiscal_year(self):
        assert self.fn("FY2024-25") == "2024"

    def test_fiscal_year_no_prefix(self):
        assert self.fn("2024-25") == "2024"

    def test_bare_year(self):
        assert self.fn("2024") == "2024"

    def test_empty_returns_none(self):
        assert self.fn("") is None

    def test_dash_returns_none(self):
        assert self.fn("-") is None

    def test_na_returns_none(self):
        assert self.fn("N/A") is None


# ---------------------------------------------------------------------------
# _parse_wb_period helper
# ---------------------------------------------------------------------------

class TestParseWBPeriod:
    @pytest.fixture(autouse=True)
    def _fn(self):
        from extraction.adapters.macro.worldbank import _parse_wb_period
        self.fn = _parse_wb_period

    def test_annual(self):
        period, ptype = self.fn("2023")
        assert period == "2023"
        assert ptype == "annual"

    def test_quarterly(self):
        period, ptype = self.fn("2023Q3")
        assert period == "2023-Q3"
        assert ptype == "quarterly"

    def test_monthly(self):
        period, ptype = self.fn("2023M06")
        assert period == "2023-06"
        assert ptype == "monthly"

    def test_unknown_passthrough(self):
        period, ptype = self.fn("FY2023")
        assert ptype == "unknown"


# ---------------------------------------------------------------------------
# BangladeshBankAdapter normalize() — synthetic HTML tables
# ---------------------------------------------------------------------------

def _make_bb_html(rows: list[tuple[str, str]]) -> str:
    tr_rows = "\n".join(
        f"<tr><td>{period}</td><td>{value}</td></tr>" for period, value in rows
    )
    return f"""
    <html><body>
    <table>
      <tr><th>Date</th><th>Rate</th></tr>
      {tr_rows}
    </table>
    </body></html>
    """


class TestBBNormalizePolicyRate:
    @pytest.fixture(scope="class")
    def df(self):
        from extraction.adapters.macro.bangladesh_bank import BangladeshBankAdapter
        html = _make_bb_html([
            ("2024-07", "8.50"),
            ("2024-06", "8.00"),
            ("2024-05", "7.75"),
        ])
        return BangladeshBankAdapter(indicator="policy_rate").normalize(html)

    def test_has_rows(self, df: pd.DataFrame):
        assert len(df) >= 1

    def test_required_columns(self, df: pd.DataFrame):
        assert {"indicator", "value", "unit", "period", "source", "fetched_at"} <= set(df.columns)

    def test_indicator_name(self, df: pd.DataFrame):
        assert (df["indicator"] == "policy_rate").all()

    def test_unit(self, df: pd.DataFrame):
        assert (df["unit"] == "percent").all()

    def test_value_decimal(self, df: pd.DataFrame):
        assert all(isinstance(v, Decimal) for v in df["value"])

    def test_value_positive(self, df: pd.DataFrame):
        assert (df["value"] > 0).all()

    def test_source(self, df: pd.DataFrame):
        assert (df["source"] == "bb_policy_rate").all()

    def test_fetched_at_utc(self, df: pd.DataFrame):
        assert df["fetched_at"].iloc[0].tzinfo is not None


class TestBBNormalizeCPI:
    @pytest.fixture(scope="class")
    def df(self):
        from extraction.adapters.macro.bangladesh_bank import BangladeshBankAdapter
        html = _make_bb_html([
            ("July 2024", "9.72"),
            ("Jun-24",    "9.89"),
            ("May 2024",  "9.89"),
        ])
        return BangladeshBankAdapter(indicator="cpi").normalize(html)

    def test_has_rows(self, df: pd.DataFrame):
        assert len(df) >= 1

    def test_indicator_name(self, df: pd.DataFrame):
        assert (df["indicator"] == "cpi").all()

    def test_value_decimal(self, df: pd.DataFrame):
        assert all(isinstance(v, Decimal) for v in df["value"])

    def test_period_format(self, df: pd.DataFrame):
        # Periods should be YYYY-MM or YYYY
        import re
        for p in df["period"]:
            assert re.match(r"^\d{4}(-\d{2})?$", p), f"unexpected period: {p!r}"


class TestBBNormalizeUsdBdt:
    @pytest.fixture(scope="class")
    def df(self):
        from extraction.adapters.macro.bangladesh_bank import BangladeshBankAdapter
        html = _make_bb_html([
            ("15/07/2024", "110.50"),
            ("14/07/2024", "110.45"),
        ])
        return BangladeshBankAdapter(indicator="usd_bdt").normalize(html)

    def test_has_rows(self, df: pd.DataFrame):
        assert len(df) >= 1

    def test_unit(self, df: pd.DataFrame):
        assert (df["unit"] == "bdt_per_usd").all()

    def test_value_above_50(self, df: pd.DataFrame):
        # BDT/USD should be ~100-120 currently
        assert (df["value"] > 50).all()


class TestBBNormalizeRemittance:
    @pytest.fixture(scope="class")
    def df(self):
        from extraction.adapters.macro.bangladesh_bank import BangladeshBankAdapter
        html = _make_bb_html([
            ("July 2024", "1950.00"),
            ("Jun-24",    "2540.00"),
        ])
        return BangladeshBankAdapter(indicator="remittance").normalize(html)

    def test_has_rows(self, df: pd.DataFrame):
        assert len(df) >= 1

    def test_unit(self, df: pd.DataFrame):
        assert (df["unit"] == "usd_million").all()

    def test_value_positive(self, df: pd.DataFrame):
        assert (df["value"] > 0).all()


# ---------------------------------------------------------------------------
# BangladeshBankAdapter normalize() — raw HTML fixture (best-effort)
# ---------------------------------------------------------------------------

class TestBBFixturePolicyRate:
    @pytest.fixture(scope="class")
    def df(self):
        raw_html = _load("bb_policy_rate_raw_html")
        from extraction.adapters.macro.bangladesh_bank import BangladeshBankAdapter
        try:
            return BangladeshBankAdapter(indicator="policy_rate").normalize(raw_html)
        except Exception:
            pytest.skip("BB policy_rate HTML fixture doesn't contain parseable table (CAPTCHA or truncated)")

    def test_has_rows(self, df: pd.DataFrame):
        assert len(df) > 0

    def test_schema(self, df: pd.DataFrame):
        assert {"indicator", "value", "period", "unit", "source"} <= set(df.columns)


# ---------------------------------------------------------------------------
# WorldBankAdapter normalize() — synthetic JSON input
# ---------------------------------------------------------------------------

class TestWBNormalizeSynthetic:
    @pytest.fixture(scope="class")
    def df(self):
        from extraction.adapters.macro.worldbank import WorldBankAdapter
        raw = [
            {"page": 1, "pages": 1, "per_page": 12, "total": 3},
            [
                {"date": "2023", "value": 9.02, "country": {"id": "BD"}},
                {"date": "2022", "value": 7.70, "country": {"id": "BD"}},
                {"date": "2021", "value": 5.56, "country": {"id": "BD"}},
                {"date": "2020", "value": None, "country": {"id": "BD"}},
            ],
        ]
        return WorldBankAdapter(indicator="FP.CPI.TOTL.ZG").normalize(raw)

    def test_row_count(self, df: pd.DataFrame):
        # None value filtered out
        assert len(df) == 3

    def test_required_columns(self, df: pd.DataFrame):
        assert {"indicator", "value", "unit", "period", "period_type", "source", "fetched_at"} <= set(df.columns)

    def test_indicator_name(self, df: pd.DataFrame):
        assert (df["indicator"] == "cpi").all()

    def test_unit(self, df: pd.DataFrame):
        assert (df["unit"] == "percent").all()

    def test_value_decimal(self, df: pd.DataFrame):
        assert all(isinstance(v, Decimal) for v in df["value"])

    def test_period_type_annual(self, df: pd.DataFrame):
        assert (df["period_type"] == "annual").all()

    def test_period_format(self, df: pd.DataFrame):
        assert list(df["period"]) == ["2023", "2022", "2021"]

    def test_none_values_excluded(self, df: pd.DataFrame):
        assert df["value"].notna().all()

    def test_source_contains_indicator(self, df: pd.DataFrame):
        assert "fp_cpi_totl_zg" in df["source"].iloc[0]

    def test_fetched_at_utc(self, df: pd.DataFrame):
        assert df["fetched_at"].iloc[0].tzinfo is not None


class TestWBNormalizeBadInput:
    def test_empty_entries_raises(self):
        from extraction.adapters.macro.worldbank import WorldBankAdapter
        import pytest as _pytest
        with _pytest.raises(ValueError, match="empty data"):
            WorldBankAdapter(indicator="FP.CPI.TOTL.ZG").normalize([{}, []])

    def test_non_list_raises(self):
        from extraction.adapters.macro.worldbank import WorldBankAdapter
        import pytest as _pytest
        with _pytest.raises(ValueError):
            WorldBankAdapter(indicator="FP.CPI.TOTL.ZG").normalize({"bad": "input"})


# ---------------------------------------------------------------------------
# WorldBankAdapter — fixture-based schema tests (already-normalized DataFrames)
# ---------------------------------------------------------------------------

class TestWBFixtureCPI:
    @pytest.fixture(scope="class")
    def df(self):
        return _load("wb_FP_CPI_TOTL_ZG_df")

    def test_has_rows(self, df: pd.DataFrame):
        assert len(df) > 0

    def test_required_columns(self, df: pd.DataFrame):
        assert {"indicator", "value", "period", "unit", "source", "fetched_at"} <= set(df.columns)

    def test_indicator_is_cpi(self, df: pd.DataFrame):
        assert (df["indicator"] == "cpi").all()

    def test_value_decimal(self, df: pd.DataFrame):
        assert all(isinstance(v, Decimal) for v in df["value"].dropna())

    def test_fetched_at_utc(self, df: pd.DataFrame):
        assert df["fetched_at"].iloc[0].tzinfo is not None


class TestWBFixtureRemittance:
    @pytest.fixture(scope="class")
    def df(self):
        return _load("wb_BX_TRF_PWKR_CD_DT_df")

    def test_has_rows(self, df: pd.DataFrame):
        assert len(df) > 0

    def test_indicator_is_remittance(self, df: pd.DataFrame):
        assert (df["indicator"] == "remittance").all()

    def test_value_positive(self, df: pd.DataFrame):
        assert (df["value"] > 0).all()


class TestWBFixtureGDP:
    @pytest.fixture(scope="class")
    def df(self):
        return _load("wb_NY_GDP_MKTP_KD_ZG_df")

    def test_has_rows(self, df: pd.DataFrame):
        assert len(df) > 0

    def test_indicator_is_gdp(self, df: pd.DataFrame):
        assert (df["indicator"] == "gdp").all()


class TestWBFixtureUsdBdt:
    @pytest.fixture(scope="class")
    def df(self):
        return _load("wb_PA_NUS_FCRF_df")

    def test_has_rows(self, df: pd.DataFrame):
        assert len(df) > 0

    def test_indicator_is_usd_bdt(self, df: pd.DataFrame):
        assert (df["indicator"] == "usd_bdt").all()

    def test_unit_bdt_per_usd(self, df: pd.DataFrame):
        assert (df["unit"] == "bdt_per_usd").all()

    def test_value_above_50(self, df: pd.DataFrame):
        # BDT/USD has been >50 since long before 2018
        assert (df["value"] > 50).all()
