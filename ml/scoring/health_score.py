"""Composite Stock Health Score (0–100)."""
from __future__ import annotations

from typing import Optional


WEIGHTS = {
    "fundamental": 0.35,
    "momentum":    0.35,
    "valuation":   0.20,
    "sentiment":   0.10,
}
NEUTRAL = 0.5  # default when a sub-score is missing


def compute_health_score(
    fundamental_score: Optional[float] = None,
    momentum_score: Optional[float] = None,
    valuation_score: Optional[float] = None,
    sentiment_score: Optional[float] = None,
) -> float:
    """
    Compute composite health score 0–100.

    Missing sub-scores are treated as NEUTRAL (0.5) so they neither help nor
    hurt. All sub-scores should be in [0, 1] — values outside are clipped.
    """
    scores = {
        "fundamental": min(max(NEUTRAL if fundamental_score is None else fundamental_score, 0.0), 1.0),
        "momentum":    min(max(NEUTRAL if momentum_score    is None else momentum_score,    0.0), 1.0),
        "valuation":   min(max(NEUTRAL if valuation_score   is None else valuation_score,   0.0), 1.0),
        "sentiment":   min(max(NEUTRAL if sentiment_score   is None else sentiment_score,   0.0), 1.0),
    }
    composite = sum(scores[k] * w for k, w in WEIGHTS.items())
    return round(min(max(composite * 100, 0.0), 100.0), 2)
