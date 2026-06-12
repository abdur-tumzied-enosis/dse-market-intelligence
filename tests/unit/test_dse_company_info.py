"""Offline unit tests for DSE displayCompany parsing — fixtures from test_dse_company_smoke.py."""
from __future__ import annotations

import pickle
from datetime import date
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

FIXTURES = Path(__file__).parent.parent / "fixtures"

_CITYBANK = FIXTURES / "dse_company_CITYBANK.pkl"
pytestmark = pytest.mark.skipif(not _CITYBANK.exists(), reason="run tests/smoke/test_dse_company_smoke.py first")


def _soup(ticker: str) -> BeautifulSoup:
    html = pickle.loads((FIXTURES / f"dse_company_{ticker}.pkl").read_bytes())
    return BeautifulSoup(html, "lxml")


@pytest.fixture(scope="module")
def citybank() -> BeautifulSoup:
    return _soup("CITYBANK")


class TestExpandHeaderGrid:
    def test_eps_nav_table_labels(self, citybank):
        from extraction.adapters.dse_direct.company_info import _expand_header_grid, _find_eps_nav_table

        tbl = _find_eps_nav_table(citybank)
        labels = _expand_header_grid(tbl)

        assert labels, "no header labels extracted"
        joined = " | ".join(labels)
        assert "profit for the year" in joined
        assert "nav per share" in joined
        # every label is lowercase, hierarchical parts joined with ' > '
        assert all(lab == lab.lower() for lab in labels)

    def test_grid_handles_all_fixture_layouts(self):
        from extraction.adapters.dse_direct.company_info import _expand_header_grid, _find_eps_nav_table

        for ticker in ("CITYBANK", "GP", "SQURPHARMA", "FAMILYTEX"):
            tbl = _find_eps_nav_table(_soup(ticker))
            if tbl is None:  # Z-cat may lack the table entirely
                continue
            labels = _expand_header_grid(tbl)
            assert any("nav per share" in lab for lab in labels), ticker


class TestEpsNavAllYears:
    def test_citybank_profit_and_nav(self, citybank):
        from extraction.adapters.dse_direct.company_info import _parse_eps_nav_all_years

        rows = {r["fiscal_year"]: r for r in _parse_eps_nav_all_years(citybank)}

        assert 2025 in rows and 2021 in rows
        assert float(rows[2025]["net_profit_mn"]) == pytest.approx(13242.27)
        assert float(rows[2021]["net_profit_mn"]) == pytest.approx(5494.16)
        assert float(rows[2025]["nav"]) == pytest.approx(40.67)
        assert float(rows[2025]["eps"]) == pytest.approx(8.71)
        assert rows[2025]["eps_basis"] is not None

    def test_d3_no_basic_fallthrough_for_diluted(self, citybank):
        """Page shows '-' for diluted EPS — must be None, never the basic value."""
        from extraction.adapters.dse_direct.company_info import _parse_eps_nav_all_years

        rows = {r["fiscal_year"]: r for r in _parse_eps_nav_all_years(citybank)}
        assert rows[2025]["eps_diluted"] is None


class TestPeDividendAndMerge:
    """Task 5 — P/E grid parser + D2 merge fix (dividend-only years survive)."""

    def test_pe_and_yield_per_year(self, citybank):
        from extraction.adapters.dse_direct.company_info import _parse_pe_dividend_all_years

        rows = {r["fiscal_year"]: r for r in _parse_pe_dividend_all_years(citybank)}
        assert float(rows[2021]["pe"]) == pytest.approx(4.82)
        assert float(rows[2021]["dividend_yield"]) == pytest.approx(5.04)

    def test_d2_dividend_only_years_survive_merge(self, citybank):
        """CITYBANK bonus history reaches 2004 — merge must include years
        absent from the EPS table (which starts at 2021)."""
        from extraction.adapters.dse_direct.company_info import (
            _merge_yearly_rows,
            _parse_dividend_history_th_td,
            _parse_eps_nav_all_years,
            _parse_pe_dividend_all_years,
            _parse_th_td,
        )

        th_td = _parse_th_td(citybank)
        div = _parse_dividend_history_th_td(th_td.get("dividend_raw"), th_td.get("bonus_raw"))
        merged = _merge_yearly_rows(
            _parse_eps_nav_all_years(citybank),
            _parse_pe_dividend_all_years(citybank),
            div,
        )
        years = {r["fiscal_year"] for r in merged}
        assert 2004 in years and 2015 in years
        by_year = {r["fiscal_year"]: r for r in merged}
        assert float(by_year[2004]["stock_div_pct"]) == pytest.approx(50.0)
        assert float(by_year[2015]["cash_div_pct"]) == pytest.approx(22.0)
        assert by_year[2004]["eps"] is None and by_year[2004]["nav"] is None


class TestNewParsers:
    def test_shareholding_all_three_periods(self, citybank):
        from extraction.adapters.dse_direct.company_info import _parse_shareholding_all

        rows = _parse_shareholding_all(citybank)
        assert len(rows) == 3
        by_date = {r["as_on_date"]: r for r in rows}
        assert date(2025, 12, 31) in by_date
        assert date(2026, 5, 31) in by_date
        assert float(by_date[date(2025, 12, 31)]["institution_pct"]) == pytest.approx(19.33)
        assert float(by_date[date(2026, 5, 31)]["institution_pct"]) == pytest.approx(15.00)
        assert float(by_date[date(2026, 5, 31)]["sponsor_pct"]) == pytest.approx(30.37)

    def test_right_issues(self):
        from extraction.adapters.dse_direct.company_info import _parse_right_issues

        rows = _parse_right_issues("1R:1 2010, 1R:1 2004,1R:2  2003")
        assert [(r["fiscal_year"], r["ratio_text"], r["ratio"]) for r in rows] == [
            (2010, "1R:1", 1.0), (2004, "1R:1", 1.0), (2003, "1R:2", 0.5),
        ]
        assert _parse_right_issues(None) == []
        assert _parse_right_issues("") == []

    def test_status_table(self, citybank):
        from extraction.adapters.dse_direct.company_info import _parse_status_table

        s = _parse_status_table(citybank)
        assert s["operational_status"] == "Active"
        assert float(s["short_loan_mn"]) == 0
        assert float(s["long_loan_mn"]) == 11080
        assert s["loan_as_on"] == date(2025, 12, 31)

    def test_links(self, citybank):
        from extraction.adapters.dse_direct.company_info import _parse_links

        links = _parse_links(citybank)
        assert links["ir_url"] == "https://www.citybankplc.com/investor-relation"
        assert links["psi_url"] == "https://www.citybankplc.com/price-sensitive-information"

    def test_quarterly_full(self, citybank):
        from extraction.adapters.dse_direct.company_info import _parse_quarterly_eps_full

        rows = _parse_quarterly_eps_full(citybank)
        q1 = next(r for r in rows if r["quarter"] == 1)
        assert q1["fiscal_year"] == 2026
        assert float(q1["eps_basic"]) == pytest.approx(1.580)
        assert float(q1["period_end_price"]) == pytest.approx(29.7)
        # Q4 derived from Annual - 9 Months; both '-' on this page → no Q4 row
        assert all(r["quarter"] != 4 for r in rows)


class TestCompanyBundle:
    def test_bundle_from_fixture_html(self, citybank):
        import pandas as pd
        from extraction.adapters.dse_direct.company_info import (
            DSEDirectCompanyInfoAdapter,
            _build_bundle,
        )

        html = pickle.loads((FIXTURES / "dse_company_CITYBANK.pkl").read_bytes())
        bundle = _build_bundle(html, "CITYBANK", source=DSEDirectCompanyInfoAdapter.name)

        assert isinstance(bundle.yearly, pd.DataFrame) and len(bundle.yearly) >= 20
        assert "net_profit_mn" in bundle.yearly.columns
        assert len(bundle.shareholding) == 3
        assert len(bundle.quarterly) >= 1
        # actions: 11 cash + 19 bonus + 3 rights
        assert set(bundle.actions["action_type"]) == {"cash_div", "stock_div", "right_issue"}
        assert bundle.company_meta["scrip_code"] == "11102"
        assert float(bundle.company_meta["face_value"]) == pytest.approx(10.0)
        assert bundle.company_meta["market_lot"] == 1
        assert bundle.company_meta["ir_url"].endswith("investor-relation")
        assert (bundle.yearly["ticker"] == "CITYBANK").all()
