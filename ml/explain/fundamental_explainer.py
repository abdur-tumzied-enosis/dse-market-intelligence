"""Translate model output + features into a beginner-readable payload."""
from __future__ import annotations

import numpy as np
import pandas as pd

# Each entry: plain name + a value-format kind.
FEATURE_TEMPLATES: dict[str, tuple[str, str]] = {
    "eps_growth_1yr":     ("Earnings growth (1yr)", "pct"),
    "eps_growth_3yr":     ("Earnings growth (3yr)", "pct"),
    "profit_cagr_3y":     ("Profit growth (3yr)", "pct"),
    "profit_cagr_5y":     ("Profit growth (5yr)", "pct"),
    "nav_growth":         ("Book value growth", "pct"),
    "quarterly_eps_yoy":  ("Latest quarter vs last year", "pct"),
    "eps_consistency":    ("Earnings stability", "ratio"),
    "roe":                ("Return on equity", "pct"),
    "earnings_quality":   ("Earnings quality", "ratio"),
    "pe_vs_sector":       ("Valuation vs sector (P/E)", "x"),
    "pb_ratio":           ("Price-to-book", "x"),
    "div_yield":          ("Dividend yield", "pct"),
    "dividend_yield_pct": ("Dividend yield", "pct"),
    "dividend_streak":    ("Years of continuous dividends", "years"),
    "cash_div_ratio_5y":  ("Cash-dividend share (5yr)", "ratio"),
    "payout_ratio":       ("Dividend payout ratio", "ratio"),
    "rights_count_10y":   ("Rights issues (10yr)", "count"),
    "inst_flow_pp":       ("Institutional ownership change", "pp"),
    "foreign_flow_pp":    ("Foreign ownership change", "pp"),
    "institution_pct":    ("Institutional ownership", "pct_level"),
    "foreign_pct":        ("Foreign ownership", "pct_level"),
}


def _format_value(kind: str, value: float) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    if kind == "pct":
        return f"{value * 100:.0f}%"
    if kind in ("pct_level", "pp"):
        return f"{value:.1f}%"
    if kind == "x":
        return f"{value:.2f}x"
    if kind in ("years", "count"):
        return f"{value:.0f}"
    return f"{value:.2f}"


def build_explanation(
    headline: float,
    pillars: dict[str, float],
    feature_row: pd.Series,
    contributions: pd.Series,
    top_k: int = 3,
) -> dict:
    """Assemble the beginner payload.

    headline: 0-100 score. pillars: pillar -> 0-100. feature_row: raw feature values.
    contributions: per-feature SHAP value (signed) for this row. Drivers are the
    top_k features by |contribution|; polarity is good if contribution > 0.
    """
    ranked = contributions.reindex(
        contributions.abs().sort_values(ascending=False, kind="stable").index)
    drivers = []
    for feat in ranked.index[:top_k]:
        name, kind = FEATURE_TEMPLATES.get(feat, (feat, "ratio"))
        raw = feature_row.get(feat, np.nan)
        raw_val = None if pd.isna(raw) else float(raw)
        polarity = "good" if ranked[feat] > 0 else "bad"
        adverb = "boosts" if polarity == "good" else "drags down"
        sentence = f"{name}: {_format_value(kind, raw)} — {adverb} the score."
        drivers.append({"feature": feat, "value": raw_val, "sentence": sentence,
                        "polarity": polarity})
    return {
        "headline": float(headline),
        "pillars": {k: float(v) for k, v in pillars.items()},
        "drivers": drivers,
    }


def build_scorecard_explanation(
    health_score: float | None,
    pillars: dict[str, float | None],
    feature_row: pd.Series,
    feature_percentiles: pd.Series,
    ml_score: float | None = None,
    top_k_strength: int = 2,
    top_k_weak: int = 1,
) -> dict:
    """Beginner payload from the deterministic scorecard (no model dependency).

    Drivers are the strongest (highest direction-adjusted percentile) and weakest
    features for this ticker. feature_percentiles: Series indexed by feature, values
    in 0-1 (higher = better, already direction-adjusted). Sentences cite peer rank.
    """
    fp = feature_percentiles.dropna()
    drivers: list[dict] = []
    if not fp.empty:
        ranked = fp.sort_values(ascending=False)
        strengths = list(ranked.index[:top_k_strength])
        # weaknesses: lowest, excluding any already chosen as strengths
        weak_candidates = [f for f in ranked.index if f not in strengths]
        weaknesses = weak_candidates[-top_k_weak:] if weak_candidates else []
        for feat in strengths:
            drivers.append(_scorecard_driver(feat, feature_row, float(fp[feat]), good=True))
        for feat in weaknesses:
            drivers.append(_scorecard_driver(feat, feature_row, float(fp[feat]), good=False))
    return {
        "health_score": float(health_score) if health_score is not None else None,
        "ml_score": float(ml_score) if ml_score is not None else None,
        "pillars": {k: (float(v) if v is not None else None) for k, v in pillars.items()},
        "drivers": drivers,
    }


def _scorecard_driver(feat: str, feature_row: pd.Series, adj_pct: float, good: bool) -> dict:
    name, kind = FEATURE_TEMPLATES.get(feat, (feat, "ratio"))
    raw = feature_row.get(feat, np.nan)
    raw_val = None if pd.isna(raw) else float(raw)
    if good:
        top_pct = max(1, round((1.0 - adj_pct) * 100))
        sentence = f"{name}: {_format_value(kind, raw)} — top {top_pct}% of peers."
        polarity = "good"
    else:
        bottom_pct = max(1, round(adj_pct * 100))
        sentence = f"{name}: {_format_value(kind, raw)} — bottom {bottom_pct}% of peers."
        polarity = "bad"
    return {"feature": feat, "value": raw_val, "sentence": sentence,
            "polarity": polarity, "percentile": round(adj_pct, 3)}
