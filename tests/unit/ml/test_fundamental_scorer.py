"""Tests for ml.models.fundamental_scorer."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml.models.fundamental_scorer import FEATURE_COLS


def _make_training_data(n: int = 300) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(42)
    X = pd.DataFrame(rng.standard_normal((n, len(FEATURE_COLS))), columns=FEATURE_COLS)
    logits = X.iloc[:, :3].sum(axis=1)
    y = pd.Series((logits + rng.standard_normal(n) > 0).astype(int))
    return X, y


def test_feature_cols_has_21_features():
    assert len(FEATURE_COLS) == 21


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


def test_shap_contributions_shape():
    from ml.models.fundamental_scorer import FundamentalScorer
    scorer = FundamentalScorer()
    X, y = _make_training_data()
    scorer.fit(X, y)
    contribs = scorer.shap_contributions(X.iloc[:5])
    assert contribs.shape == (5, len(FEATURE_COLS))
    assert list(contribs.columns) == FEATURE_COLS


def test_save_load_roundtrip_preserves_feature_cols():
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
        assert loaded.feature_cols == FEATURE_COLS
    np.testing.assert_array_almost_equal(proba_before, proba_after)


def test_fit_handles_single_class_training_split():
    from ml.models.fundamental_scorer import FundamentalScorer
    # first 80% all class 1, last 20% mixed -> training split is single-class
    n = 100
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.standard_normal((n, len(FEATURE_COLS))), columns=FEATURE_COLS)
    y = pd.Series([1] * 80 + [0, 1] * 10)
    scorer = FundamentalScorer()
    scorer.fit(X, y)  # must not raise
    proba = scorer.predict_proba(X)
    assert proba.shape == (n,)
    assert (proba >= 0).all() and (proba <= 1).all()
