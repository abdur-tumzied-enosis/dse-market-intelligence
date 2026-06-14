"""Unit tests for cross-sectional momentum scoring."""
from __future__ import annotations

import pandas as pd
import pytest

from ml.scoring.momentum_score import _MIN_BARS, compute_momentum_scores


def _series(values: list[float]) -> pd.Series:
    idx = pd.date_range("2025-01-01", periods=len(values), freq="D")
    return pd.Series(values, index=idx, dtype=float)


def _trend(start: float, daily_pct: float, n: int = 200) -> pd.Series:
    """Geometric price path with a constant daily drift."""
    return _series([start * (1 + daily_pct) ** i for i in range(n)])


def test_rising_outranks_falling() -> None:
    scores = compute_momentum_scores({
        "UP": _trend(100, 0.004),
        "FLAT": _trend(100, 0.0),
        "DOWN": _trend(100, -0.004),
    })
    assert scores["UP"] > scores["FLAT"] > scores["DOWN"]


def test_scores_bounded_0_1() -> None:
    scores = compute_momentum_scores({
        "UP": _trend(100, 0.01),
        "DOWN": _trend(100, -0.01),
        "MID": _trend(100, 0.001),
    })
    assert all(0.0 <= v <= 1.0 for v in scores.values())


def test_centered_on_global_average() -> None:
    """A ticker with the cohort's average path lands at ~0.5 (market-relative center)."""
    scores = compute_momentum_scores({
        "A": _trend(100, 0.006),
        "B": _trend(100, 0.002),   # mid of the three drifts
        "C": _trend(100, -0.002),
    })
    assert scores["B"] == pytest.approx(0.5, abs=0.05)


def test_short_history_omitted() -> None:
    scores = compute_momentum_scores({
        "LONG": _trend(100, 0.003),
        "LONG2": _trend(100, -0.003),
        "SHORT": _series([10.0] * (_MIN_BARS - 1)),
    })
    assert "SHORT" not in scores
    assert {"LONG", "LONG2"} <= set(scores)


def test_zero_and_negative_closes_filtered() -> None:
    vals = [0.0, -5.0] + [100 * (1.003) ** i for i in range(200)]
    scores = compute_momentum_scores({
        "DIRTY": _series(vals),
        "CLEAN": _trend(100, -0.003),
    })
    assert 0.0 <= scores["DIRTY"] <= 1.0  # survived the cleanup, scored normally


def test_too_few_tickers_returns_empty() -> None:
    assert compute_momentum_scores({"ONLY": _trend(100, 0.003)}) == {}


def test_missing_long_window_treated_neutral() -> None:
    """A ticker with only ~80 bars (no 126d return) still scores without error."""
    scores = compute_momentum_scores({
        "MIDLEN": _trend(100, 0.005, n=80),
        "FULL": _trend(100, -0.001, n=200),
    })
    assert "MIDLEN" in scores
    assert 0.0 <= scores["MIDLEN"] <= 1.0
