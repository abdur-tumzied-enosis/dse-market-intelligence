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

    def test_pe_grid_year_2021(self, citybank):
        """PE table: year 2021 → pe=4.82, dividend_yield=5.04."""
        from extraction.adapters.dse_direct.company_info import _parse_pe_dividend_all_years

        rows = {r["fiscal_year"]: r for r in _parse_pe_dividend_all_years(citybank)}
        assert 2021 in rows
        assert float(rows[2021]["pe"]) == pytest.approx(4.82)
        assert float(rows[2021]["dividend_yield"]) == pytest.approx(5.04)

    def test_pe_grid_all_table_years_present(self, citybank):
        """All 5 years in CITYBANK P/E table (2021-2025) must be returned."""
        from extraction.adapters.dse_direct.company_info import _parse_pe_dividend_all_years

        rows = _parse_pe_dividend_all_years(citybank)
        years = {r["fiscal_year"] for r in rows}
        assert {2021, 2022, 2023, 2024, 2025} <= years

    def test_d2_dividend_only_years_survive_merge(self, citybank):
        """D2 fix: dividend-only years (e.g. 2004 bonus=50%) appear in merged output."""
        from extraction.adapters.dse_direct.company_info import (
            _merge_yearly_rows,
            _parse_dividend_history_th_td,
            _parse_eps_nav_all_years,
            _parse_pe_dividend_all_years,
            _parse_th_td,
        )

        eps_rows = _parse_eps_nav_all_years(citybank)
        pe_rows = _parse_pe_dividend_all_years(citybank)
        th_td = _parse_th_td(citybank)
        div_by_year = _parse_dividend_history_th_td(
            th_td.get("dividend_raw"), th_td.get("bonus_raw")
        )

        merged = {r["fiscal_year"]: r for r in _merge_yearly_rows(eps_rows, pe_rows, div_by_year)}

        # D2: bonus-only year 2004 must appear (was dropped before this fix)
        assert 2004 in merged, "dividend-only year 2004 missing from merged output"
        assert float(merged[2004]["stock_div_pct"]) == pytest.approx(50.0)
        assert merged[2004]["cash_div_pct"] is None

        # Cash-only year 2015 also present
        assert 2015 in merged
        assert float(merged[2015]["cash_div_pct"]) == pytest.approx(22.0)

    def test_merge_carries_eps_basis_and_profit(self, citybank):
        """Merged rows carry eps_basis, net_profit_mn, tci_mn from EPS table."""
        from extraction.adapters.dse_direct.company_info import (
            _merge_yearly_rows,
            _parse_dividend_history_th_td,
            _parse_eps_nav_all_years,
            _parse_pe_dividend_all_years,
            _parse_th_td,
        )

        eps_rows = _parse_eps_nav_all_years(citybank)
        pe_rows = _parse_pe_dividend_all_years(citybank)
        th_td = _parse_th_td(citybank)
        div_by_year = _parse_dividend_history_th_td(
            th_td.get("dividend_raw"), th_td.get("bonus_raw")
        )

        merged = {r["fiscal_year"]: r for r in _merge_yearly_rows(eps_rows, pe_rows, div_by_year)}

        assert merged[2025]["eps_basis"] is not None
        assert float(merged[2025]["net_profit_mn"]) == pytest.approx(13242.27)
        # dividend_yield from PE table propagated
        assert float(merged[2021]["dividend_yield"]) == pytest.approx(5.04)


class TestNewParsers:
    """Task 6 — shareholding history, right issues, status table, links, quarterly EPS."""

    # ------------------------------------------------------------------
    # _parse_as_on_date
    # ------------------------------------------------------------------
    def test_parse_as_on_date_dec(self):
        from extraction.adapters.dse_direct.company_info import _parse_as_on_date

        assert _parse_as_on_date("Dec 31, 2025") == date(2025, 12, 31)

    def test_parse_as_on_date_apr(self):
        from extraction.adapters.dse_direct.company_info import _parse_as_on_date

        assert _parse_as_on_date("Apr 30, 2026") == date(2026, 4, 30)

    def test_parse_as_on_date_none_on_garbage(self):
        from extraction.adapters.dse_direct.company_info import _parse_as_on_date

        assert _parse_as_on_date("not a date") is None

    # ------------------------------------------------------------------
    # _parse_shareholding_all
    # ------------------------------------------------------------------
    def test_shareholding_all_count(self, citybank):
        from extraction.adapters.dse_direct.company_info import _parse_shareholding_all

        rows = _parse_shareholding_all(citybank)
        assert len(rows) == 3

    def test_shareholding_all_first_row_institute(self, citybank):
        """Dec 31 2025 row: institute=19.33, sponsor=30.37."""
        from extraction.adapters.dse_direct.company_info import _parse_shareholding_all

        rows = _parse_shareholding_all(citybank)
        first = rows[0]
        assert first["shareholding_date"] == date(2025, 12, 31)
        assert float(first["institution_pct"]) == pytest.approx(19.33)
        assert float(first["sponsor_pct"]) == pytest.approx(30.37)

    def test_shareholding_all_last_row(self, citybank):
        """May 31 2026 row: institute=15.00, sponsor=30.37."""
        from extraction.adapters.dse_direct.company_info import _parse_shareholding_all

        rows = _parse_shareholding_all(citybank)
        last = rows[-1]
        assert last["shareholding_date"] == date(2026, 5, 31)
        assert float(last["institution_pct"]) == pytest.approx(15.00)
        assert float(last["sponsor_pct"]) == pytest.approx(30.37)

    # ------------------------------------------------------------------
    # _parse_right_issues
    # ------------------------------------------------------------------
    def test_right_issues_count(self, citybank):
        """CITYBANK has 3 right issues."""
        from extraction.adapters.dse_direct.company_info import _parse_right_issues

        issues = _parse_right_issues(citybank)
        assert len(issues) == 3

    def test_right_issues_values(self, citybank):
        """Issues: 1R:1 2010, 1R:1 2004, 1R:2 2003."""
        from extraction.adapters.dse_direct.company_info import _parse_right_issues

        issues = _parse_right_issues(citybank)
        by_year = {r["year"]: r for r in issues}
        assert by_year[2010]["ratio"] == "1:1"
        assert by_year[2004]["ratio"] == "1:1"
        assert by_year[2003]["ratio"] == "1:2"

    # ------------------------------------------------------------------
    # _parse_status_table
    # ------------------------------------------------------------------
    def test_status_table_active(self, citybank):
        from extraction.adapters.dse_direct.company_info import _parse_status_table

        st = _parse_status_table(citybank)
        assert st["status"] == "Active"

    def test_status_table_loans(self, citybank):
        from extraction.adapters.dse_direct.company_info import _parse_status_table

        st = _parse_status_table(citybank)
        assert float(st["short_loan_mn"]) == pytest.approx(0)
        assert float(st["long_loan_mn"]) == pytest.approx(11080)

    def test_status_table_as_on(self, citybank):
        from extraction.adapters.dse_direct.company_info import _parse_status_table

        st = _parse_status_table(citybank)
        assert st["loan_as_on"] == date(2025, 12, 31)

    # ------------------------------------------------------------------
    # _parse_links
    # ------------------------------------------------------------------
    def test_links_ir(self, citybank):
        from extraction.adapters.dse_direct.company_info import _parse_links

        links = _parse_links(citybank)
        assert links["ir_url"] == "https://www.citybankplc.com/investor-relation"

    def test_links_psi(self, citybank):
        from extraction.adapters.dse_direct.company_info import _parse_links

        links = _parse_links(citybank)
        assert links["psi_url"] == "https://www.citybankplc.com/price-sensitive-information"

    # ------------------------------------------------------------------
    # _parse_quarterly_eps_full
    # ------------------------------------------------------------------
    def test_quarterly_full_fy_tag(self, citybank):
        from extraction.adapters.dse_direct.company_info import _parse_quarterly_eps_full

        rows = _parse_quarterly_eps_full(citybank)
        assert len(rows) >= 1
        assert rows[0]["fy_tag"] == "202603"

    def test_quarterly_full_q1_eps(self, citybank):
        from extraction.adapters.dse_direct.company_info import _parse_quarterly_eps_full

        rows = _parse_quarterly_eps_full(citybank)
        r = rows[0]
        assert float(r["eps_basic"]) == pytest.approx(1.580)

    def test_quarterly_full_period_end_price(self, citybank):
        from extraction.adapters.dse_direct.company_info import _parse_quarterly_eps_full

        rows = _parse_quarterly_eps_full(citybank)
        r = rows[0]
        assert float(r["period_end_price"]) == pytest.approx(29.7)

    def test_quarterly_full_no_q4(self, citybank):
        """Annual column is '-' → eps_annual must be None."""
        from extraction.adapters.dse_direct.company_info import _parse_quarterly_eps_full

        rows = _parse_quarterly_eps_full(citybank)
        r = rows[0]
        assert r["eps_annual"] is None

    def test_quarterly_full_no_9months(self, citybank):
        """9 Months column is '-' → eps_9m must be None."""
        from extraction.adapters.dse_direct.company_info import _parse_quarterly_eps_full

        rows = _parse_quarterly_eps_full(citybank)
        r = rows[0]
        assert r["eps_9m"] is None
