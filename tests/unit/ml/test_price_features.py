"""Tests for ml.features.price_features."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

REQUIRED_COLS = [
    "rsi_14", "macd_diff", "bb_pband", "atr_norm", "adx_14",
    "return_1d", "return_5d", "return_20d", "volume_zscore", "obv",
    "gap_open", "body", "mid_return",
]


def _make_ohlcv(n: int = 70) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    price = 100 + rng.standard_normal(n).cumsum()
    price = np.abs(price) + 10  # ensure positive
    close = price
    open_ = close + rng.uniform(-1, 1, n)
    return pd.DataFrame(
        {
            "open": open_,
            "close": close,
            "high": np.maximum(close, open_) + rng.uniform(0.5, 2, n),
            "low": np.minimum(close, open_) - rng.uniform(0.5, 2, n),
            "volume": rng.integers(100_000, 1_000_000, n).astype(float),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="D"),
    )


def test_returns_required_columns():
    from ml.features.price_features import compute_price_features
    result = compute_price_features(_make_ohlcv())
    for col in REQUIRED_COLS:
        assert col in result.columns, f"missing column: {col}"


def test_row_count_preserved():
    from ml.features.price_features import compute_price_features
    df = _make_ohlcv(70)
    result = compute_price_features(df)
    assert len(result) == 70


def test_no_infinite_values():
    from ml.features.price_features import compute_price_features
    result = compute_price_features(_make_ohlcv())
    feat_cols = [c for c in REQUIRED_COLS if c in result.columns]
    assert not result[feat_cols].isin([np.inf, -np.inf]).any().any()


def test_rsi_range_0_to_100():
    from ml.features.price_features import compute_price_features
    result = compute_price_features(_make_ohlcv())
    valid = result["rsi_14"].dropna()
    assert (valid >= 0).all() and (valid <= 100).all()


def test_atr_norm_positive():
    from ml.features.price_features import compute_price_features
    result = compute_price_features(_make_ohlcv())
    valid = result["atr_norm"].dropna()
    assert (valid >= 0).all()


def test_open_based_features_present():
    from ml.features.price_features import compute_price_features
    result = compute_price_features(_make_ohlcv())
    for col in ["gap_open", "body", "mid_return"]:
        assert col in result.columns, f"missing column: {col}"


def test_body_matches_formula():
    from ml.features.price_features import compute_price_features
    df = _make_ohlcv()
    result = compute_price_features(df)
    expected = (df["close"] - df["open"]) / df["open"]
    pd.testing.assert_series_equal(
        result["body"], expected, check_names=False
    )
