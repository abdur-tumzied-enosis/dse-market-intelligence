"""Tests for ml.inference.score_fundamentals."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest


def _make_scorer_mock(proba: float = 0.7):
    """Mock FundamentalScorer returning fixed probability + zero SHAP contributions."""
    import pandas as pd

    from ml.models.fundamental_scorer import FEATURE_COLS
    scorer = MagicMock()
    scorer.predict_proba = MagicMock(side_effect=lambda X: np.full(len(X), proba))
    scorer.shap_contributions = MagicMock(
        side_effect=lambda X: pd.DataFrame(
            [[0.0] * len(FEATURE_COLS)] * len(X), columns=FEATURE_COLS, index=X.index))
    return scorer


@pytest.mark.asyncio
async def test_score_returns_dict_per_ticker():
    from ml.inference.score_fundamentals import score_all_tickers
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[
        {"ticker": "GP"}, {"ticker": "BRACBANK"}
    ])

    from ml.models.fundamental_scorer import FEATURE_COLS
    feature_vec = pd.Series({c: 0.5 for c in FEATURE_COLS})

    scorer = _make_scorer_mock(0.65)

    with patch("ml.inference.score_fundamentals.build_fundamental_feature_vector",
               AsyncMock(return_value=feature_vec)):
        results = await score_all_tickers(pool, scorer)

    assert "GP" in results
    assert "BRACBANK" in results
    assert 0.0 <= results["GP"]["score"] <= 1.0


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


@pytest.mark.asyncio
async def test_score_attaches_pillars_and_drivers():
    from ml.inference.score_fundamentals import score_all_tickers
    from ml.models.fundamental_scorer import FEATURE_COLS
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[{"ticker": "GP"}, {"ticker": "BANK"}])

    def _vec(seed):
        return pd.Series({c: float(seed) for c in FEATURE_COLS})

    scorer = MagicMock()
    scorer.predict_proba = MagicMock(side_effect=lambda X: np.full(len(X), 0.7))
    scorer.shap_contributions = MagicMock(
        side_effect=lambda X: pd.DataFrame(
            [[0.1] * len(FEATURE_COLS)] * len(X), columns=FEATURE_COLS, index=X.index))

    with patch("ml.inference.score_fundamentals.build_fundamental_feature_vector",
               AsyncMock(side_effect=[_vec(1), _vec(2)])):
        results = await score_all_tickers(pool, scorer)

    assert "GP" in results and "BANK" in results
    assert "score" in results["GP"] and "pillars" in results["GP"]
    assert "drivers" in results["GP"]
    assert set(results["GP"]["pillars"].keys())  # non-empty pillar dict
