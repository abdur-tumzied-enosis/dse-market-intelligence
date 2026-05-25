"""Tests for ml.features.macro_features."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _make_macro(n: int = 30) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="ME")
    return pd.DataFrame({
        "usd_bdt":     110.0 + np.arange(n) * 0.1,
        "policy_rate": 8.0 + np.zeros(n),
        "cpi":         150.0 + np.arange(n) * 0.5,
    }, index=idx)


def test_usd_bdt_change_20d_present():
    from ml.features.macro_features import compute_macro_features
    result = compute_macro_features(_make_macro())
    assert "usd_bdt_change_20d" in result.columns


def test_policy_rate_delta_present():
    from ml.features.macro_features import compute_macro_features
    result = compute_macro_features(_make_macro())
    assert "policy_rate_delta" in result.columns


def test_cpi_trend_present():
    from ml.features.macro_features import compute_macro_features
    result = compute_macro_features(_make_macro())
    assert "cpi_trend" in result.columns


def test_handles_missing_columns_gracefully():
    from ml.features.macro_features import compute_macro_features
    df = pd.DataFrame({"usd_bdt": [110.0, 111.0]},
                      index=pd.date_range("2024-01-01", periods=2, freq="ME"))
    result = compute_macro_features(df)
    assert "usd_bdt_change_20d" in result.columns
    assert "policy_rate_delta" not in result.columns
