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


# ---------------------------------------------------------------------------
# New metrics: brier_score, directional_hit_rate, reliability_table,
# is_low_signal
# ---------------------------------------------------------------------------

def test_brier_score_perfect_is_zero():
    from ml.eval.metrics import brier_score
    prob = np.array([1.0, 0.0, 1.0, 0.0])
    outcome = np.array([1, 0, 1, 0])
    assert brier_score(prob, outcome) == 0.0


def test_brier_score_known_value():
    from ml.eval.metrics import brier_score
    # (0.7-1)^2 + (0.2-0)^2 = 0.09 + 0.04 = 0.13; mean = 0.065
    assert abs(brier_score(np.array([0.7, 0.2]), np.array([1, 0])) - 0.065) < 1e-9


def test_directional_hit_rate_counts_sign_agreement():
    from ml.eval.metrics import directional_hit_rate
    pred = np.array([0.5, -0.2, 0.1, -3.0])   # up, down, up, down
    actual_up = np.array([1, 0, 0, 1])          # hit, hit, miss, miss
    assert directional_hit_rate(pred, actual_up) == 0.5


def test_reliability_table_bins_and_counts():
    from ml.eval.metrics import reliability_table
    prob = np.array([0.05, 0.15, 0.95, 0.85])
    outcome = np.array([0, 0, 1, 1])
    tbl = reliability_table(prob, outcome, n_bins=2)
    # bin 0 = [0,0.5): two samples, frac_pos 0; bin 1 = [0.5,1]: two, frac_pos 1
    assert list(tbl["n"]) == [2, 2]
    assert list(tbl["frac_pos"]) == [0.0, 1.0]


def test_reliability_table_empty_input_keeps_schema():
    from ml.eval.metrics import reliability_table
    tbl = reliability_table(np.array([]), np.array([]))
    assert list(tbl.columns) == ["bin", "mean_prob", "frac_pos", "n"]
    assert len(tbl) == 0


def test_is_low_signal_true_at_base_rate():
    from ml.eval.metrics import is_low_signal
    # hit == base, large n -> not distinguishable -> low signal
    assert is_low_signal(hit_rate=0.50, base_rate=0.50, n=500) is True


def test_is_low_signal_false_with_clear_edge():
    from ml.eval.metrics import is_low_signal
    # +8pp over base, large n -> real signal
    assert is_low_signal(hit_rate=0.58, base_rate=0.50, n=500) is False


def test_is_low_signal_true_when_effect_below_2pp_even_if_significant():
    from ml.eval.metrics import is_low_signal
    # tiny 1pp edge: effect-size floor flags it regardless of n
    assert is_low_signal(hit_rate=0.51, base_rate=0.50, n=100_000) is True
