"""Tests for ml.explain.fundamental_explainer."""
from __future__ import annotations

import pandas as pd


def test_build_explanation_payload_shape():
    from ml.explain.fundamental_explainer import build_explanation
    feature_row = pd.Series({
        "eps_growth_1yr": 0.18, "profit_cagr_5y": 0.22, "pe_vs_sector": 0.6,
        "roe": 0.19, "dividend_streak": 5.0, "foreign_flow_pp": 1.2,
    })
    pillars = {"growth": 88.0, "quality": 75.0, "value": 64.0,
               "dividends": 80.0, "safety": 40.0, "ownership": 70.0}
    contribs = pd.Series({
        "profit_cagr_5y": 0.9, "pe_vs_sector": 0.4, "eps_growth_1yr": 0.3,
        "roe": 0.1, "dividend_streak": -0.05, "foreign_flow_pp": 0.02,
    })
    out = build_explanation(headline=82.0, pillars=pillars,
                            feature_row=feature_row, contributions=contribs)
    assert out["headline"] == 82.0
    assert set(out["pillars"].keys()) == set(pillars.keys())
    assert len(out["drivers"]) == 3
    # top driver is the largest |contribution| feature
    assert out["drivers"][0]["feature"] == "profit_cagr_5y"
    for d in out["drivers"]:
        assert {"feature", "value", "sentence", "polarity"} <= set(d.keys())
        assert d["polarity"] in ("good", "bad")


def test_driver_polarity_follows_contribution_sign():
    from ml.explain.fundamental_explainer import build_explanation
    feature_row = pd.Series({"pe_vs_sector": 1.8, "roe": 0.02, "profit_cagr_5y": -0.1})
    contribs = pd.Series({"pe_vs_sector": -0.7, "roe": -0.3, "profit_cagr_5y": -0.2})
    out = build_explanation(headline=30.0, pillars={}, feature_row=feature_row,
                            contributions=contribs)
    assert out["drivers"][0]["feature"] == "pe_vs_sector"
    assert out["drivers"][0]["polarity"] == "bad"


def test_driver_value_is_none_when_feature_missing():
    import json

    from ml.explain.fundamental_explainer import build_explanation
    # contribution references a feature absent from feature_row -> value must be JSON-null
    feature_row = pd.Series({"roe": 0.1})
    contribs = pd.Series({"profit_cagr_5y": 0.9, "roe": 0.2})
    out = build_explanation(headline=50.0, pillars={}, feature_row=feature_row,
                            contributions=contribs)
    top = out["drivers"][0]
    assert top["feature"] == "profit_cagr_5y"
    assert top["value"] is None  # missing -> None, not NaN
    assert "n/a" in top["sentence"]
    json.dumps(out)  # must not raise (valid JSON, no NaN literal)


def test_build_scorecard_explanation_strengths_and_weakness():
    import json

    import numpy as np

    from ml.explain.fundamental_explainer import build_scorecard_explanation

    feature_row = pd.Series({
        "roe": 0.19, "profit_cagr_5y": 0.22, "pe_vs_sector": 1.8,
        "dividend_streak": 5.0, "eps_growth_1yr": np.nan,
    })
    # direction-adjusted percentiles (higher = better)
    feature_percentiles = pd.Series({
        "roe": 0.95, "profit_cagr_5y": 0.90, "pe_vs_sector": 0.10,
        "dividend_streak": 0.60, "eps_growth_1yr": np.nan,
    })
    pillars = {"growth": 88.0, "quality": 75.0, "value": 40.0}
    out = build_scorecard_explanation(
        health_score=70.0, pillars=pillars, feature_row=feature_row,
        feature_percentiles=feature_percentiles, ml_score=0.65)

    assert out["health_score"] == 70.0
    assert out["ml_score"] == 0.65
    assert set(out["pillars"].keys()) == set(pillars.keys())

    drivers = out["drivers"]
    # 2 strengths + 1 weakness
    assert len(drivers) == 3
    feats = [d["feature"] for d in drivers]
    # strengths are the top adjusted-pct features
    assert feats[0] == "roe" and feats[1] == "profit_cagr_5y"
    # weakness is the lowest adjusted-pct feature, no overlap with strengths
    assert feats[2] == "pe_vs_sector"
    assert len(feats) == len(set(feats))

    for d in drivers:
        assert {"feature", "value", "sentence", "polarity", "percentile"} <= set(d.keys())
    assert drivers[0]["polarity"] == "good" and "top" in drivers[0]["sentence"]
    assert drivers[2]["polarity"] == "bad" and "bottom" in drivers[2]["sentence"]

    json.dumps(out)  # must not raise


def test_build_scorecard_explanation_nan_value_is_none():
    import json

    import numpy as np

    from ml.explain.fundamental_explainer import build_scorecard_explanation

    feature_row = pd.Series({"roe": np.nan, "pe_vs_sector": 1.0})
    feature_percentiles = pd.Series({"roe": 0.9, "pe_vs_sector": 0.2})
    out = build_scorecard_explanation(
        health_score=None, pillars={"quality": None},
        feature_row=feature_row, feature_percentiles=feature_percentiles)
    # health_score None passes through as None
    assert out["health_score"] is None
    assert out["ml_score"] is None
    assert out["pillars"]["quality"] is None
    # roe raw is NaN -> value None
    roe_driver = next(d for d in out["drivers"] if d["feature"] == "roe")
    assert roe_driver["value"] is None
    json.dumps(out)


def test_build_scorecard_explanation_empty_percentiles():
    from ml.explain.fundamental_explainer import build_scorecard_explanation

    out = build_scorecard_explanation(
        health_score=50.0, pillars={"growth": 50.0},
        feature_row=pd.Series(dtype=float),
        feature_percentiles=pd.Series(dtype=float))
    assert out["drivers"] == []
    assert out["health_score"] == 50.0
