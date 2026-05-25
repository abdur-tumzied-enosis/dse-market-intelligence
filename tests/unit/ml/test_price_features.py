"""Tests for ml.features.price_features."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

REQUIRED_COLS = [
    "rsi_14", "macd_diff", "bb_pband", "atr_norm", "adx_14",
    "return_1d", "return_5d", "return_20d", "volume_zscore", "obv",
]


def _make_ohlcv(n: int = 70) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    price = 100 + rng.standard_normal(n).cumsum()
    price = np.abs(price) + 10  # ensure positive
    return pd.DataFrame(
        {
            "close": price,
            "high": price + rng.uniform(0.5, 2, n),
            "low": price - rng.uniform(0.5, 2, n),
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
