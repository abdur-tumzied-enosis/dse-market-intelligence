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
