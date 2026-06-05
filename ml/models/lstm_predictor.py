"""Attention-pooled LSTM for DSE cross-sectional return ranking."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import torch
import torch.nn as nn

# Regression heads: predicted rank-return for [1,2,3,5,8,13]d horizons
N_HORIZONS = 6


class AttentionPool(nn.Module):
    """Learned-query attention pooling over the time dimension."""

    def __init__(self, hidden_size: int) -> None:
        super().__init__()
        self.score = nn.Linear(hidden_size, 1)

    def forward(self, seq: torch.Tensor) -> torch.Tensor:
        # seq: (batch, time, hidden) -> (batch, hidden)
        weights = torch.softmax(self.score(seq).squeeze(-1), dim=1)  # (B, T)
        return torch.bmm(weights.unsqueeze(1), seq).squeeze(1)


class LSTMPredictor(nn.Module):
    """Front-end -> LSTM -> attention pool -> linear heads.

    Input:  (batch, seq_len, input_size) normalized features.
    Output: (batch, N_HORIZONS) regression of cross-sectional rank-return.

    `front_end` is a swappable slot (default nn.Identity) so a Conv1d block can
    be added later without changing the LSTM/attention/heads.
    """

    def __init__(
        self,
        input_size: int = 13,
        hidden_size: int = 64,
        num_layers: int = 2,
        dropout: float = 0.4,
    ) -> None:
        super().__init__()
        self._input_size = input_size
        self._hidden_size = hidden_size
        self._num_layers = num_layers

        self.front_end: nn.Module = nn.Identity()
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.pool = AttentionPool(hidden_size)
        self.head = nn.Linear(hidden_size, N_HORIZONS)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.front_end(x)
        seq_out, _ = self.lstm(x)          # (batch, seq_len, hidden)
        pooled = self.pool(seq_out)        # (batch, hidden)
        return self.head(pooled)           # (batch, N_HORIZONS)

    def predict(self, x: torch.Tensor) -> torch.Tensor:
        """Returns raw regression outputs (batch, N_HORIZONS)."""
        self.eval()
        with torch.no_grad():
            return self(x)

    def save(self, path: Path, scaler: Any = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "state_dict": self.state_dict(),
                "input_size": self._input_size,
                "hidden_size": self._hidden_size,
                "num_layers": self._num_layers,
            },
            path,
        )

    @classmethod
    def load(cls, path: Path) -> "LSTMPredictor":
        checkpoint: dict[str, Any] = torch.load(
            path, map_location="cpu", weights_only=False
        )
        model = cls(
            input_size=checkpoint["input_size"],
            hidden_size=checkpoint["hidden_size"],
            num_layers=checkpoint["num_layers"],
        )
        model.load_state_dict(checkpoint["state_dict"])
        model.eval()
        return model
