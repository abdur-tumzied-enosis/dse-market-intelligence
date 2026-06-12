"""Offline unit tests for DSE displayCompany parsing — fixtures from test_dse_company_smoke.py."""
from __future__ import annotations

import pickle
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
