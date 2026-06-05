"""Tests for ml.models.lstm_predictor."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import torch


def _make_batch(batch=4, seq=60, features=13):
    return torch.randn(batch, seq, features)


def test_forward_output_shape():
    from ml.models.lstm_predictor import LSTMPredictor
    model = LSTMPredictor(input_size=13)
    out = model(_make_batch())
    assert out.shape == (4, 6), f"expected (4,6), got {out.shape}"


def test_forward_no_nan():
    from ml.models.lstm_predictor import LSTMPredictor
    model = LSTMPredictor(input_size=13)
    assert not torch.isnan(model(_make_batch())).any()


def test_predict_returns_raw_regression():
    from ml.models.lstm_predictor import LSTMPredictor
    model = LSTMPredictor(input_size=13)
    out = model.predict(_make_batch(batch=8))
    assert out.shape == (8, 6)
    # regression output is unbounded — not constrained to [0,1]
    assert out.dtype == torch.float32


def test_front_end_defaults_to_identity():
    import torch.nn as nn
    from ml.models.lstm_predictor import LSTMPredictor
    model = LSTMPredictor(input_size=13)
    assert isinstance(model.front_end, nn.Identity)


def test_save_load_roundtrip():
    from ml.models.lstm_predictor import LSTMPredictor
    model = LSTMPredictor(input_size=13)
    x = _make_batch(batch=2)
    before = model.predict(x)
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "lstm.pt"
        model.save(path)
        loaded = LSTMPredictor.load(path)
        after = loaded.predict(x)
    np.testing.assert_array_almost_equal(before.numpy(), after.numpy(), decimal=5)
