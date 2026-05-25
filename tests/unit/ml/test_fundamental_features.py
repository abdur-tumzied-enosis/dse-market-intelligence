"""Tests for ml.features.fundamental_features."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _make_fundamentals() -> pd.DataFrame:
    return pd.DataFrame({
        "fiscal_year": [2019, 2020, 2021, 2022, 2023],
        "eps":         [5.0,  5.5,  6.0,  5.8,  7.0],
        "nav":         [50.0, 53.0, 56.0, 54.0, 60.0],
        "pe":          [12.0, 11.0, 13.0, 12.5, 10.0],
        "cash_div_pct":[20.0, 20.0, 25.0, 20.0, 30.0],
        "stock_div_pct":[0.0,  0.0,  0.0,  5.0,  0.0],
    })


def test_eps_growth_1yr():
    from ml.features.fundamental_features import compute_fundamental_features
    result = compute_fundamental_features(_make_fundamentals())
    # 2020: (5.5 - 5.0) / 5.0 = 0.1
    assert abs(result.iloc[1]["eps_growth_1yr"] - 0.1) < 1e-6


def test_nav_growth_1yr():
    from ml.features.fundamental_features import compute_fundamental_features
    result = compute_fundamental_features(_make_fundamentals())
    # 2020: (53 - 50) / 50 = 0.06
    assert abs(result.iloc[1]["nav_growth"] - 0.06) < 1e-6


def test_eps_growth_3yr_cagr():
    from ml.features.fundamental_features import compute_fundamental_features
    result = compute_fundamental_features(_make_fundamentals())
    # 2022: (5.8 / 5.0)^(1/3) - 1 ≈ 0.05
    expected = (5.8 / 5.0) ** (1 / 3) - 1
    assert abs(result.iloc[3]["eps_growth_3yr"] - expected) < 1e-6


def test_eps_consistency_bounded_0_to_1():
    from ml.features.fundamental_features import compute_fundamental_features
    result = compute_fundamental_features(_make_fundamentals())
    valid = result["eps_consistency"].dropna()
    assert (valid >= 0).all() and (valid <= 1).all()


def test_handles_zero_eps_no_inf():
    from ml.features.fundamental_features import compute_fundamental_features
    df = _make_fundamentals()
    df.loc[2, "eps"] = 0.0
    result = compute_fundamental_features(df)
    assert not result["eps_growth_1yr"].isin([np.inf, -np.inf]).any()


def test_div_yield_nonnegative():
    from ml.features.fundamental_features import compute_fundamental_features
    result = compute_fundamental_features(_make_fundamentals())
    valid = result["div_yield"].dropna()
    assert (valid >= 0).all()
