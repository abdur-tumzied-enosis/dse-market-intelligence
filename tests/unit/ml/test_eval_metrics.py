"""Tests for ml.eval.metrics."""
from __future__ import annotations

import numpy as np


def test_rank_ic_perfect_positive():
    from ml.eval.metrics import rank_ic
    pred = np.array([1.0, 2.0, 3.0, 4.0])
    actual = np.array([10.0, 20.0, 30.0, 40.0])
    assert abs(rank_ic(pred, actual) - 1.0) < 1e-9


def test_rank_ic_perfect_negative():
    from ml.eval.metrics import rank_ic
    pred = np.array([1.0, 2.0, 3.0, 4.0])
    actual = np.array([40.0, 30.0, 20.0, 10.0])
    assert abs(rank_ic(pred, actual) + 1.0) < 1e-9


def test_rank_ic_too_few_is_nan():
    from ml.eval.metrics import rank_ic
    assert np.isnan(rank_ic(np.array([1.0]), np.array([2.0])))


def test_top_n_hit_rate():
    from ml.eval.metrics import top_n_hit_rate
    # top-2 by pred = indices 3,2 (actual 0.9, 0.1); median actual = 0.15
    pred = np.array([0.0, 1.0, 2.0, 3.0])
    actual = np.array([-0.5, 0.2, 0.1, 0.9])
    # picks actual[3]=0.9 (>med) and actual[2]=0.1 (<med) -> 0.5
    assert top_n_hit_rate(pred, actual, n=2) == 0.5
