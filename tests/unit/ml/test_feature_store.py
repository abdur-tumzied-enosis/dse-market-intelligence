"""Tests for ml.features.feature_store."""
from __future__ import annotations

import datetime

import numpy as np
import pandas as pd
import pytest
from unittest.mock import AsyncMock, MagicMock


def _mock_pool_price(rows: list[dict]) -> MagicMock:
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=rows)
    return pool


def _make_price_records(n: int = 70) -> list[dict]:
    rng = np.random.default_rng(42)
    price = np.abs(100 + rng.standard_normal(n).cumsum()) + 10
    base = datetime.datetime(2024, 1, 1)
    return [
        {
            "time": base + datetime.timedelta(days=i),
            "open": float(price[i]),
            "close": float(price[i]),
            "high": float(price[i] + 1),
            "low": float(price[i] - 1),
            "volume": float(500_000),
        }
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_build_price_feature_matrix_returns_dataframe():
    from ml.features.feature_store import build_price_feature_matrix
    pool = _mock_pool_price(_make_price_records(70))
    result = await build_price_feature_matrix(pool, "GP", lookback_days=30)
    assert isinstance(result, pd.DataFrame)
    assert len(result) <= 30


@pytest.mark.asyncio
async def test_build_price_feature_matrix_empty_when_no_rows():
    from ml.features.feature_store import build_price_feature_matrix
    pool = _mock_pool_price([])
    result = await build_price_feature_matrix(pool, "GP", lookback_days=30)
    assert result.empty


@pytest.mark.asyncio
async def test_build_price_feature_matrix_no_nans_after_fill():
    from ml.features.feature_store import build_price_feature_matrix
    pool = _mock_pool_price(_make_price_records(70))
    result = await build_price_feature_matrix(pool, "GP", lookback_days=30)
    if not result.empty:
        assert not result.isnull().any().any()


@pytest.mark.asyncio
async def test_price_features_smoothed_like_training():
    """build_price_feature_matrix must EMA-smooth features identically to the
    training pipeline (clean_and_smooth), preventing train/serve skew."""
    from ml.constants import EMA_SPAN, PRICE_FEATURE_COLS
    from ml.features.cross_sectional import clean_and_smooth
    from ml.features.feature_store import build_price_feature_matrix
    from ml.features.price_features import compute_price_features

    records = _make_price_records(90)
    pool = _mock_pool_price(records)
    result = await build_price_feature_matrix(pool, "GP", lookback_days=30)

    # Recompute the expected training-side features from the same raw records
    raw = pd.DataFrame(records).set_index("time").sort_index().astype(float)
    feats_full = compute_price_features(raw)
    expected = clean_and_smooth(feats_full, PRICE_FEATURE_COLS, EMA_SPAN).tail(len(result))

    pd.testing.assert_frame_equal(
        result[PRICE_FEATURE_COLS].reset_index(drop=True),
        expected[PRICE_FEATURE_COLS].reset_index(drop=True),
        check_dtype=False,
    )
