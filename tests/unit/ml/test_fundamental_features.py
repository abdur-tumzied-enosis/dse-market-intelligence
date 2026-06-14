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
    import pandas as pd
    import pytest

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


def test_compute_leverage_features_uses_market_cap_and_profit():
    from ml.features.fundamental_features import compute_leverage_features
    f = compute_leverage_features(
        short_loan_mn=200.0, long_loan_mn=800.0,
        market_cap_bdt=5_000_000_000.0, net_profit_bdt=500_000_000.0,
    )
    # total loan = 1000 mn = 1.0e9 bdt
    assert abs(f["leverage_mktcap"] - (1.0e9 / 5.0e9)) < 1e-9
    assert abs(f["leverage_profit"] - (1.0e9 / 5.0e8)) < 1e-9


def test_compute_leverage_features_missing_loan_is_nan_not_zero():
    import math
    from ml.features.fundamental_features import compute_leverage_features
    f = compute_leverage_features(
        short_loan_mn=None, long_loan_mn=None,
        market_cap_bdt=5_000_000_000.0, net_profit_bdt=500_000_000.0,
    )
    assert math.isnan(f["leverage_mktcap"])
    assert math.isnan(f["leverage_profit"])


def test_quarterly_eps_yoy():
    import pandas as pd
    from ml.features.fundamental_features import compute_quarterly_eps_yoy
    q = pd.DataFrame({
        "fiscal_year": [2023, 2023, 2024, 2024],
        "quarter":     [1, 2, 1, 2],
        "eps_basic":   [1.0, 1.2, 1.5, 1.6],
    })
    # latest quarter = 2024Q2 (1.6); same quarter prior year = 2023Q2 (1.2)
    yoy = compute_quarterly_eps_yoy(q)
    assert abs(yoy - (1.6 / 1.2 - 1)) < 1e-9


def test_quarterly_eps_yoy_nan_when_no_prior_year_quarter():
    import math
    import pandas as pd
    from ml.features.fundamental_features import compute_quarterly_eps_yoy
    q = pd.DataFrame({"fiscal_year": [2024], "quarter": [2], "eps_basic": [1.6]})
    assert math.isnan(compute_quarterly_eps_yoy(q))


def test_earnings_quality_in_features():
    from ml.features.fundamental_features import compute_fundamental_features
    df = _make_fundamentals()
    df["net_profit_bdt"] = [100e6, 110e6, 120e6, 100e6, 140e6]
    df["total_comprehensive_income_bdt"] = [90e6, 110e6, 130e6, 80e6, 140e6]
    result = compute_fundamental_features(df)
    # 2023: 140/140 = 1.0
    assert abs(result.iloc[4]["earnings_quality"] - 1.0) < 1e-6


def test_compute_pillar_scores_ranges_and_direction():
    import pandas as pd
    from ml.features.fundamental_features import compute_pillar_scores, PILLAR_SPEC

    # 3 tickers, growth ascending; safety: lower leverage = better
    cross = pd.DataFrame({
        "eps_growth_1yr": [0.0, 0.1, 0.2], "eps_growth_3yr": [0.0, 0.1, 0.2],
        "profit_cagr_3y": [0.0, 0.1, 0.2], "profit_cagr_5y": [0.0, 0.1, 0.2],
        "nav_growth": [0.0, 0.1, 0.2], "quarterly_eps_yoy": [0.0, 0.1, 0.2],
        "eps_consistency": [0.5, 0.6, 0.7], "roe": [0.05, 0.1, 0.15],
        "earnings_quality": [0.8, 0.9, 1.0],
        "pe_vs_sector": [2.0, 1.0, 0.5], "pb_ratio": [3.0, 2.0, 1.0],
        "div_yield": [0.0, 0.02, 0.04], "dividend_yield_pct": [0.0, 2.0, 4.0],
        "dividend_streak": [0, 2, 5], "cash_div_ratio_5y": [0.0, 0.5, 1.0],
        "payout_ratio": [0.0, 0.3, 0.6],
        "leverage_mktcap": [1.0, 0.5, 0.1], "leverage_profit": [10.0, 5.0, 1.0],
        "rights_count_10y": [3, 1, 0],
        "inst_flow_pp": [-1.0, 0.0, 2.0], "foreign_flow_pp": [-1.0, 0.0, 2.0],
        "institution_pct": [10.0, 20.0, 30.0], "foreign_pct": [0.0, 5.0, 10.0],
    })
    pillars = compute_pillar_scores(cross)
    assert set(pillars.columns) == set(PILLAR_SPEC.keys())
    # all scores within 0..100
    assert ((pillars >= 0) & (pillars <= 100)).all().all()
    # ticker index 2 is best on every pillar (highest growth, lowest leverage, etc.)
    assert pillars.loc[2, "growth"] == pillars["growth"].max()
    assert pillars.loc[2, "safety"] == pillars["safety"].max()
    assert pillars.loc[2, "value"] == pillars["value"].max()
