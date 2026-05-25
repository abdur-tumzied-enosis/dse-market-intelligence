"""Tests for ml.inference.score_fundamentals."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_scorer_mock(proba: float = 0.7):
    """Mock FundamentalScorer that returns fixed probability."""
    scorer = MagicMock()
    scorer.predict_proba = MagicMock(return_value=np.array([proba]))
    return scorer


@pytest.mark.asyncio
async def test_score_returns_dict_per_ticker():
    from ml.inference.score_fundamentals import score_all_tickers
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[
        {"ticker": "GP"}, {"ticker": "BRACBANK"}
    ])

    feature_vec = pd.Series({
        "eps_growth_1yr": 0.1, "eps_growth_3yr": 0.05,
        "nav_growth": 0.06, "pe_vs_sector": 0.9,
        "div_yield": 0.03, "eps_consistency": 0.8,
    })

    scorer = _make_scorer_mock(0.65)

    with patch("ml.inference.score_fundamentals.build_fundamental_feature_vector",
               AsyncMock(return_value=feature_vec)):
        results = await score_all_tickers(pool, scorer)

    assert "GP" in results
    assert "BRACBANK" in results
    assert 0.0 <= results["GP"] <= 1.0


@pytest.mark.asyncio
async def test_score_skips_ticker_with_empty_features():
    from ml.inference.score_fundamentals import score_all_tickers
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[{"ticker": "NOBDATA"}])
    scorer = _make_scorer_mock()

    with patch("ml.inference.score_fundamentals.build_fundamental_feature_vector",
               AsyncMock(return_value=pd.Series(dtype=float))):
        results = await score_all_tickers(pool, scorer)

    assert "NOBDATA" not in results
