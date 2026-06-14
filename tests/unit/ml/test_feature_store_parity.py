"""Guards train/serve feature parity for the fundamental model."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest


@pytest.mark.asyncio
async def test_serve_vector_covers_all_feature_cols():
    from ml.features.feature_store import build_fundamental_feature_vector
    from ml.models.fundamental_scorer import FEATURE_COLS

    fund_rows = [
        {"fiscal_year": 2022, "eps": 5.0, "nav": 50.0, "pe": 12.0,
         "cash_div_pct": 20.0, "stock_div_pct": 0.0, "net_profit_bdt": 1e8,
         "total_comprehensive_income_bdt": 1e8, "dividend_yield_pct": 2.0,
         "institution_pct": 20.0, "foreign_pct": 5.0,
         "price_at_fy_end": 60.0, "median_pe": 11.0},
        {"fiscal_year": 2023, "eps": 6.0, "nav": 55.0, "pe": 10.0,
         "cash_div_pct": 25.0, "stock_div_pct": 0.0, "net_profit_bdt": 1.2e8,
         "total_comprehensive_income_bdt": 1.2e8, "dividend_yield_pct": 2.5,
         "institution_pct": 22.0, "foreign_pct": 6.0,
         "price_at_fy_end": 66.0, "median_pe": 10.5},
    ]
    pool = MagicMock()
    # Call order in build_fundamental_feature_vector:
    #   pool.fetch (main rows), pool.fetchval (rights), pool.fetchrow (flows),
    #   pool.fetch (quarterly rows).
    pool.fetch = AsyncMock(side_effect=[fund_rows, []])  # main query, then quarterly
    pool.fetchval = AsyncMock(return_value=1)            # rights count
    # flows query now also returns latest-snapshot ownership LEVELS
    pool.fetchrow = AsyncMock(return_value={
        "inst_flow": 2.0, "foreign_flow": 1.0,
        "institution_pct": 22.0, "foreign_pct": 6.0,
    })

    vec = await build_fundamental_feature_vector(pool, "TESTCO")
    assert set(FEATURE_COLS).issubset(set(vec.index))
    # Leverage feeds the Safety pillar as extra (non-FEATURE_COLS) keys.
    assert {"leverage_mktcap", "leverage_profit"}.issubset(set(vec.index))


@pytest.mark.asyncio
async def test_serve_vector_computes_leverage_when_present():
    from ml.features.feature_store import build_fundamental_feature_vector

    fund_rows = [
        {"fiscal_year": 2022, "eps": 5.0, "nav": 50.0, "pe": 12.0,
         "cash_div_pct": 20.0, "stock_div_pct": 0.0, "net_profit_bdt": 9e7,
         "total_comprehensive_income_bdt": 9e7, "dividend_yield_pct": 2.0,
         "institution_pct": 20.0, "foreign_pct": 5.0,
         "price_at_fy_end": 60.0, "median_pe": 11.0,
         "short_loan_mn": 150.0, "long_loan_mn": 700.0, "market_cap_bdt": 4e9},
        # Latest row: short+long = 1000 mn -> 1e9 BDT total debt.
        {"fiscal_year": 2023, "eps": 6.0, "nav": 55.0, "pe": 10.0,
         "cash_div_pct": 25.0, "stock_div_pct": 0.0, "net_profit_bdt": 1e8,
         "total_comprehensive_income_bdt": 1e8, "dividend_yield_pct": 2.5,
         "institution_pct": 22.0, "foreign_pct": 6.0,
         "price_at_fy_end": 66.0, "median_pe": 10.5,
         "short_loan_mn": 200.0, "long_loan_mn": 800.0, "market_cap_bdt": 5e9},
    ]
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=[fund_rows, []])  # main query, then quarterly
    pool.fetchval = AsyncMock(return_value=0)
    pool.fetchrow = AsyncMock(return_value=None)

    vec = await build_fundamental_feature_vector(pool, "TESTCO")
    # total debt = (200+800)mn * 1e6 = 1e9 BDT
    # leverage_profit = 1e9 / 1e8 = 10.0 ; leverage_mktcap = 1e9 / 5e9 = 0.2
    assert np.isfinite(vec["leverage_profit"])
    assert vec["leverage_profit"] == pytest.approx(10.0)
    assert vec["leverage_mktcap"] == pytest.approx(0.2)


def test_feature_cols_no_dupes():
    from ml.models.fundamental_scorer import FEATURE_COLS
    assert len(FEATURE_COLS) == len(set(FEATURE_COLS))
