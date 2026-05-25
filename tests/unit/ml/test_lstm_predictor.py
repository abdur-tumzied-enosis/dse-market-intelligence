"""Tests for ml.models.lstm_predictor."""
from __future__ import annotations

import numpy as np
import pytest
import torch
from pathlib import Path
import tempfile


def _make_batch(batch=4, seq=60, features=10):
    return torch.randn(batch, seq, features)


def test_forward_output_shape():
    from ml.models.lstm_predictor import LSTMPredictor
    model = LSTMPredictor(input_size=10)
    x = _make_batch()
    out = model(x)
    assert out.shape == (4, 3), f"expected (4,3), got {out.shape}"


def test_forward_no_nan():
    from ml.models.lstm_predictor import LSTMPredictor
    model = LSTMPredictor(input_size=10)
    x = _make_batch()
    out = model(x)
    assert not torch.isnan(out).any()


def test_predict_proba_range():
    from ml.models.lstm_predictor import LSTMPredictor
    model = LSTMPredictor(input_size=10)
    x = _make_batch(batch=8)
    proba = model.predict_proba(x)
    assert proba.shape == (8, 3)
    assert (proba >= 0).all() and (proba <= 1).all()


def test_save_load_roundtrip():
    from ml.models.lstm_predictor import LSTMPredictor
    model = LSTMPredictor(input_size=10)
    x = _make_batch(batch=2)
    out_before = model.predict_proba(x)

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "lstm.pt"
        model.save(path)
        loaded = LSTMPredictor.load(path)
        out_after = loaded.predict_proba(x)

    np.testing.assert_array_almost_equal(
        out_before.numpy(), out_after.numpy(), decimal=5
    )
