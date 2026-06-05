"""Tests for ml.features.cross_sectional."""
from __future__ import annotations

import numpy as np
import pandas as pd


def test_forward_returns_formula():
    from ml.features.cross_sectional import forward_returns
    close = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0])
    out = forward_returns(close, [1, 2])
    # fwd_ret_1[t] = close[t+1]/close[t] - 1
    assert out["fwd_ret_1"].iloc[0] == 11.0 / 10.0 - 1.0
    assert out["fwd_ret_2"].iloc[0] == 12.0 / 10.0 - 1.0
    # tail rows have no future -> NaN
    assert np.isnan(out["fwd_ret_1"].iloc[-1])


def test_cross_sectional_zscore_zero_mean():
    from ml.features.cross_sectional import cross_sectional_zscore
    df = pd.DataFrame({
        "time": ["d1", "d1", "d1", "d2", "d2", "d2"],
        "f": [1.0, 2.0, 3.0, 10.0, 20.0, 30.0],
    })
    out = cross_sectional_zscore(df, ["f"], by="time")
    # each date's z-scored values sum to ~0
    for _, grp in out.groupby("time"):
        assert abs(grp["f"].mean()) < 1e-9


def test_cross_sectional_zscore_constant_group_is_zero():
    from ml.features.cross_sectional import cross_sectional_zscore
    df = pd.DataFrame({"time": ["d1", "d1"], "f": [5.0, 5.0]})
    out = cross_sectional_zscore(df, ["f"], by="time")
    # zero std -> filled with 0, not NaN/inf
    assert (out["f"] == 0.0).all()


def test_build_windows_shapes_and_alignment():
    from ml.features.cross_sectional import build_windows
    # 2 tickers, 8 days each, 2 features, 1 label
    rows = []
    for tk in ["A", "B"]:
        for d in range(8):
            rows.append({
                "time": d, "ticker": tk,
                "f1": float(d), "f2": float(d * 2),
                "y_1": float(d), "raw_1": float(d) / 100,
            })
    panel = pd.DataFrame(rows)
    X, y, meta = build_windows(
        panel, feature_cols=["f1", "f2"],
        label_cols=["y_1"], raw_cols=["raw_1"], seq_len=3,
    )
    # rows per ticker with full window AND non-nan label: t in [2..7] = 6
    assert X.shape == (12, 3, 2)
    assert y.shape == (12, 1)
    assert len(meta) == 12
    assert set(meta["ticker"]) == {"A", "B"}
    assert "raw_1" in meta.columns
    # first A window covers days 0,1,2 in feature 1
    a0 = X[0, :, 0]
    np.testing.assert_array_equal(a0, np.array([0.0, 1.0, 2.0], dtype=np.float32))
