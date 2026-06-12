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


def test_track_record_features():
    import pytest
    import pandas as pd
    from ml.features.fundamental_features import compute_track_record_features

    yearly = pd.DataFrame({
        "fiscal_year":   [2021, 2022, 2023, 2024, 2025],
        "net_profit_bdt": [5494.16e6, 4781.26e6, 6384.66e6, 10143.46e6, 13242.27e6],
        "cash_div_pct":  [12.5, 10.0, 15.0, 12.5, 15.0],
        "stock_div_pct": [12.5, 2.0, 10.0, 12.5, 15.0],
    })
    f = compute_track_record_features(yearly, rights_count_10y=1,
                                      inst_flow_pp=-4.33, foreign_flow_pp=-0.61)
    # CAGR 3y: (13242.27 / 6384.66) ** (1/2)? No — window is last 4 closed years:
    # (13242.27/4781.26)^(1/3) - 1 ≈ 0.4043
    assert f["profit_cagr_3y"] == pytest.approx(0.4043, abs=1e-3)
    assert f["dividend_streak"] == 5
    assert f["cash_div_ratio_5y"] == pytest.approx(65.0 / (65.0 + 52.0), abs=1e-4)
    assert f["rights_count_10y"] == 1
    assert f["inst_flow_pp"] == pytest.approx(-4.33)


def test_track_record_cagr_null_on_sign_change():
    import math
    import pandas as pd
    from ml.features.fundamental_features import compute_track_record_features

    yearly = pd.DataFrame({
        "fiscal_year":   [2022, 2023, 2024, 2025],
        "net_profit_bdt": [-100e6, 50e6, 80e6, 120e6],
        "cash_div_pct":  [0, 0, 5.0, 5.0],
        "stock_div_pct": [0, 0, 0, 0],
    })
    f = compute_track_record_features(yearly, rights_count_10y=0,
                                      inst_flow_pp=None, foreign_flow_pp=None)
    assert math.isnan(f["profit_cagr_3y"])
    assert f["dividend_streak"] == 2
