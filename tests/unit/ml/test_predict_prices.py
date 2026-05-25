"""Tests for ml.inference.predict_prices."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import torch
from unittest.mock import AsyncMock, MagicMock, patch


def _make_feature_matrix(n: int = 70) -> pd.DataFrame:
    cols = [
        "rsi_14", "macd_diff", "bb_pband", "atr_norm", "adx_14",
        "return_1d", "return_5d", "return_20d", "volume_zscore", "obv",
        "close",
    ]
    rng = np.random.default_rng(0)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame(rng.standard_normal((n, len(cols))), columns=cols, index=idx)


@pytest.mark.asyncio
async def test_predict_writes_three_horizons():
    from ml.inference.predict_prices import predict_ticker
    pool = MagicMock()
    pool.execute = AsyncMock()

    feat_df = _make_feature_matrix(70)
    model = MagicMock()
    model.predict_proba = MagicMock(
        return_value=torch.tensor([[0.6, 0.55, 0.52]])
    )

    with patch("ml.inference.predict_prices.build_price_feature_matrix",
               AsyncMock(return_value=feat_df)):
        await predict_ticker(pool, model, "GP")

    # Should call pool.execute 3 times (one per horizon: 5d, 10d, 20d)
    assert pool.execute.call_count == 3


@pytest.mark.asyncio
async def test_predict_skips_ticker_no_data():
    from ml.inference.predict_prices import predict_ticker
    pool = MagicMock()
    pool.execute = AsyncMock()
    model = MagicMock()

    with patch("ml.inference.predict_prices.build_price_feature_matrix",
               AsyncMock(return_value=pd.DataFrame())):
        await predict_ticker(pool, model, "NOBDATA")

    pool.execute.assert_not_called()
