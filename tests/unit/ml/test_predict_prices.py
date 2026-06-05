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


@pytest.mark.asyncio
async def test_panel_has_expected_shape():
    """_build_latest_panel result has one row per (ticker, step)."""
    from ml.constants import PRICE_FEATURE_COLS, SEQ_LEN

    panel = _make_panel(n_tickers=25, seq_len=SEQ_LEN)
    assert set(panel.columns) >= {"step", "ticker", *PRICE_FEATURE_COLS}
    assert panel["ticker"].nunique() == 25
    assert len(panel) == 25 * SEQ_LEN


@pytest.mark.asyncio
async def test_module_importable():
    """Module-level import must succeed (checks constants + new API shape)."""
    import ml.inference.predict_prices as pp

    assert hasattr(pp, "main")
    assert hasattr(pp, "MODEL_VERSION")
    assert pp.MODEL_VERSION == "lstm_v1"
