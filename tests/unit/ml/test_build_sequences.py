"""Tests for train_lstm.build_sequences (DB-free, pure DataFrame in)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _fake_price_df(n_tickers=25, n_days=120):
    rng = np.random.default_rng(7)
    frames = []
    for k in range(n_tickers):
        base = 50 + k
        close = base + rng.standard_normal(n_days).cumsum()
        close = np.abs(close) + 5
        open_ = close + rng.uniform(-1, 1, n_days)
        frames.append(pd.DataFrame({
            "ticker": f"T{k:03d}",
            "time": pd.date_range("2023-01-01", periods=n_days, freq="D"),
            "open": open_,
            "close": close,
            "high": np.maximum(close, open_) + rng.uniform(0.2, 1.5, n_days),
            "low": np.minimum(close, open_) - rng.uniform(0.2, 1.5, n_days),
            "volume": rng.integers(1e5, 1e6, n_days).astype(float),
        }))
    return pd.concat(frames, ignore_index=True)


def test_build_sequences_shapes():
    from ml.features.sequence_builder import build_sequences
    from ml.constants import SEQ_LEN, HORIZONS, PRICE_FEATURE_COLS
    X, y, meta = build_sequences(_fake_price_df())
    assert X.ndim == 3
    assert X.shape[1] == SEQ_LEN
    assert X.shape[2] == len(PRICE_FEATURE_COLS)
    assert y.shape[1] == len(HORIZONS)
    assert len(meta) == X.shape[0]
    for h in HORIZONS:
        assert f"fwd_ret_{h}" in meta.columns
    assert {"time", "ticker"}.issubset(meta.columns)


def test_build_sequences_no_nan_in_features():
    from ml.features.sequence_builder import build_sequences
    X, y, meta = build_sequences(_fake_price_df())
    assert not np.isnan(X).any()
    assert not np.isnan(y).any()


def test_build_sequences_labels_are_cross_sectional_zscored():
    from ml.features.sequence_builder import build_sequences
    X, y, meta = build_sequences(_fake_price_df())
    df = meta.copy()
    df["y0"] = y[:, 0]
    means = df.groupby("time")["y0"].mean()
    assert means.abs().mean() < 0.2
