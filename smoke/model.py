"""1-layer GRU classifier for the smoke pipeline."""

from __future__ import annotations

import torch
from torch import nn


class GRUClassifier(nn.Module):
    def __init__(self, input_dim: int = 10, hidden_dim: int = 64, num_layers: int = 1):
        super().__init__()
        self.gru = nn.GRU(
            input_size=input_dim,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
        )
        self.head = nn.Linear(hidden_dim, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, WINDOW, F) -> last-timestep hidden state -> logit
        out, _ = self.gru(x)
        last = out[:, -1, :]  # (B, hidden)
        return self.head(last).squeeze(-1)  # (B,) raw logits
