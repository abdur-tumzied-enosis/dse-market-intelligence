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


def brier_score(prob: np.ndarray, outcome: np.ndarray) -> float:
    """Mean squared error between predicted P(up) and the 0/1 outcome."""
    prob = np.asarray(prob, dtype=float)
    outcome = np.asarray(outcome, dtype=float)
    return float(np.mean((prob - outcome) ** 2))


def directional_hit_rate(pred: np.ndarray, actual_up: np.ndarray) -> float:
    """Fraction where the predicted up/down call (pred >= 0 == 'up') matches the
    realized direction (actual_up is 1 if the forward return was > 0)."""
    pred = np.asarray(pred, dtype=float)
    actual_up = np.asarray(actual_up, dtype=int)
    call_up = (pred >= 0).astype(int)
    return float((call_up == actual_up).mean())


def reliability_table(
    prob: np.ndarray, outcome: np.ndarray, n_bins: int = 10
) -> pd.DataFrame:
    """Per-bin mean predicted prob vs realized frequency — the calibration curve.
    Returns a DataFrame with columns [bin, mean_prob, frac_pos, n]; empty bins
    are dropped."""
    prob = np.asarray(prob, dtype=float)
    outcome = np.asarray(outcome, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    # clip to [0, n_bins-1] so prob==1.0 lands in the last bin
    idx = np.clip(np.digitize(prob, edges[1:-1], right=False), 0, n_bins - 1)
    rows = []
    for b in range(n_bins):
        m = idx == b
        if not m.any():
            continue
        rows.append(
            {
                "bin": b,
                "mean_prob": float(prob[m].mean()),
                "frac_pos": float(outcome[m].mean()),
                "n": int(m.sum()),
            }
        )
    return pd.DataFrame(rows)


def is_low_signal(hit_rate: float, base_rate: float, n: int) -> bool:
    """A horizon is 'low signal' if its directional edge over the base rate is
    not worth shipping. Flagged (True) if EITHER the one-sided binomial test
    cannot reject 'hit == base' at p>0.10, OR the effect size is < 2pp. OR-combined
    so it suppresses conservatively (overlapping forward windows inflate the
    binomial significance, so the effect-size floor is the real guard)."""
    from scipy.stats import binomtest

    if abs(hit_rate - base_rate) < 0.02:
        return True
    successes = int(round(hit_rate * n))
    p = binomtest(successes, n, base_rate, alternative="greater").pvalue
    return bool(p > 0.10)
