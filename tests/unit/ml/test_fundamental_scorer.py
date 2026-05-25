"""Tests for ml.models.fundamental_scorer."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pathlib import Path
import tempfile


FEATURE_COLS = [
    "eps_growth_1yr", "eps_growth_3yr", "nav_growth",
    "pe_vs_sector", "div_yield", "eps_consistency",
]


def _make_training_data(n: int = 100) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(42)
    X = pd.DataFrame(rng.standard_normal((n, len(FEATURE_COLS))), columns=FEATURE_COLS)
    y = pd.Series((rng.random(n) > 0.5).astype(int))
    return X, y


def test_predict_proba_shape():
    from ml.models.fundamental_scorer import FundamentalScorer
    scorer = FundamentalScorer()
    X, y = _make_training_data()
    scorer.fit(X, y)
    proba = scorer.predict_proba(X)
    assert proba.shape == (len(X),)


def test_predict_proba_range():
    from ml.models.fundamental_scorer import FundamentalScorer
    scorer = FundamentalScorer()
    X, y = _make_training_data()
    scorer.fit(X, y)
    proba = scorer.predict_proba(X)
    assert (proba >= 0).all() and (proba <= 1).all()


def test_predict_before_fit_raises():
    from ml.models.fundamental_scorer import FundamentalScorer
    scorer = FundamentalScorer()
    X, _ = _make_training_data(10)
    with pytest.raises(RuntimeError, match="not trained"):
        scorer.predict_proba(X)


def test_save_load_roundtrip():
    from ml.models.fundamental_scorer import FundamentalScorer
    scorer = FundamentalScorer()
    X, y = _make_training_data()
    scorer.fit(X, y)
    proba_before = scorer.predict_proba(X)

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "scorer.pkl"
        scorer.save(path)
        loaded = FundamentalScorer()
        loaded.load(path)
        proba_after = loaded.predict_proba(X)

    np.testing.assert_array_almost_equal(proba_before, proba_after)


def test_feature_importances_keys():
    from ml.models.fundamental_scorer import FundamentalScorer
    scorer = FundamentalScorer()
    X, y = _make_training_data()
    scorer.fit(X, y)
    imps = scorer.feature_importances()
    assert set(imps.keys()) == set(FEATURE_COLS)


def test_handles_nan_features():
    from ml.models.fundamental_scorer import FundamentalScorer
    scorer = FundamentalScorer()
    X, y = _make_training_data()
    scorer.fit(X, y)
    X_nan = X.copy()
    X_nan.iloc[0, 0] = np.nan
    proba = scorer.predict_proba(X_nan)
    assert proba.shape == (len(X_nan),)
