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
