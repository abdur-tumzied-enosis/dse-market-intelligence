"""2-layer LSTM for DSE price direction prediction."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import torch
import torch.nn as nn

# 3 output heads: P(up) for 5d / 10d / 20d horizons
N_HORIZONS = 3


class LSTMPredictor(nn.Module):
    """
    2-layer LSTM predicting probability of price increase over 3 horizons.

    Input: (batch, seq_len, input_size) tensor of normalized price features.
    Output: (batch, 3) logits for [5d_up, 10d_up, 20d_up].
    """

    def __init__(
        self,
        input_size: int = 10,
        hidden_size: int = 64,
        num_layers: int = 2,
        dropout: float = 0.2,
    ) -> None:
        super().__init__()
        self._input_size = input_size
        self._hidden_size = hidden_size
        self._num_layers = num_layers

        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.head = nn.Linear(hidden_size, N_HORIZONS)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Returns logits (batch, 3). Apply sigmoid for probabilities."""
        _, (h_n, _) = self.lstm(x)
        last_hidden = h_n[-1]  # (batch, hidden_size)
        return self.head(last_hidden)

    def predict_proba(self, x: torch.Tensor) -> torch.Tensor:
        """Returns probabilities (batch, 3) in [0, 1]."""
        self.eval()
        with torch.no_grad():
            return torch.sigmoid(self(x))

    def save(self, path: Path, scaler: Optional[Any] = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "state_dict": self.state_dict(),
                "input_size": self._input_size,
                "hidden_size": self._hidden_size,
                "num_layers": self._num_layers,
                "scaler": scaler,
            },
            path,
        )

    @classmethod
    def load(cls, path: Path) -> "tuple[LSTMPredictor, Any]":
        """Returns (model, scaler). scaler is None if checkpoint predates scaling."""
        checkpoint: dict[str, Any] = torch.load(path, map_location="cpu", weights_only=False)
        model = cls(
            input_size=checkpoint["input_size"],
            hidden_size=checkpoint["hidden_size"],
            num_layers=checkpoint["num_layers"],
        )
        model.load_state_dict(checkpoint["state_dict"])
        model.eval()
        return model, checkpoint.get("scaler")
