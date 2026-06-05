"""Signal-quality metrics for cross-sectional return ranking."""
from __future__ import annotations

import numpy as np
import pandas as pd


def rank_ic(pred: np.ndarray, actual: np.ndarray) -> float:
    """Spearman rank correlation between predictions and realized returns.

    Computed as Pearson correlation of the ranks. Returns NaN if fewer than
    2 valid (non-NaN) pairs or if either side has zero rank variance.
    """
    pred = np.asarray(pred, dtype=float)
    actual = np.asarray(actual, dtype=float)
    mask = ~(np.isnan(pred) | np.isnan(actual))
    if mask.sum() < 2:
        return float("nan")
    pr = pd.Series(pred[mask]).rank().to_numpy()
    ar = pd.Series(actual[mask]).rank().to_numpy()
    if pr.std() == 0 or ar.std() == 0:
        return float("nan")
    return float(np.corrcoef(pr, ar)[0, 1])


def top_n_hit_rate(pred: np.ndarray, actual: np.ndarray, n: int) -> float:
    """Fraction of the top-`n` predicted names whose realized return beats the
    cross-sectional median realized return."""
    pred = np.asarray(pred, dtype=float)
    actual = np.asarray(actual, dtype=float)
    if len(pred) == 0:
        return float("nan")
    k = min(n, len(pred))
    top_idx = np.argsort(pred)[::-1][:k]
    median = np.median(actual)
    return float((actual[top_idx] > median).mean())
