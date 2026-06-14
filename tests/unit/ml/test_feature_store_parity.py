"""Guards train/serve feature parity for the fundamental model."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

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
    pool.fetchrow = AsyncMock(return_value={"inst_flow": 2.0, "foreign_flow": 1.0})

    vec = await build_fundamental_feature_vector(pool, "TESTCO")
    assert set(FEATURE_COLS).issubset(set(vec.index))


def test_feature_cols_no_dupes():
    from ml.models.fundamental_scorer import FEATURE_COLS
    assert len(FEATURE_COLS) == len(set(FEATURE_COLS))
