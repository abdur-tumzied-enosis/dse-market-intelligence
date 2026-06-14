"""Tests for ml.inference.predict_prices (cross-sectional inference)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _make_panel(n_tickers: int = 25, seq_len: int = 60) -> pd.DataFrame:
    from ml.constants import PRICE_FEATURE_COLS

    rng = np.random.default_rng(1)
    rows = []
    for ti in range(n_tickers):
        ticker = f"TK{ti:02d}"
        for step in range(seq_len):
            row = {"step": step, "ticker": ticker}
            for col in PRICE_FEATURE_COLS:
                row[col] = rng.standard_normal()
            rows.append(row)
    return pd.DataFrame(rows)


def test_cross_sectional_zscore_by_step_normalizes_each_step():
    """Inference path z-scores features across tickers per aligned `step`.
    Exercise that production call and assert each step is zero-mean."""
    from ml.constants import PRICE_FEATURE_COLS, SEQ_LEN
    from ml.features.cross_sectional import cross_sectional_zscore

    panel = _make_panel(n_tickers=25, seq_len=SEQ_LEN)
    normed = cross_sectional_zscore(panel, PRICE_FEATURE_COLS, by="step")
    # every step's cross-section is mean-centered for each feature
    per_step_mean = normed.groupby("step")[PRICE_FEATURE_COLS].mean().abs()
    assert (per_step_mean < 1e-9).all().all()
    assert not normed[PRICE_FEATURE_COLS].isna().any().any()


@pytest.mark.asyncio
async def test_module_importable():
    """Module-level import must succeed (checks constants + new API shape)."""
    import ml.inference.predict_prices as pp

    assert hasattr(pp, "main")
    assert hasattr(pp, "MODEL_VERSION")
    assert pp.MODEL_VERSION == "lstm_v2_cal"
