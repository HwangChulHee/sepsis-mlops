"""Windowing + torch Dataset.

Method A (timestep labels): each length-8, stride-1 COMPLETE window's target is the
SepsisLabel at the window's LAST hour (off-by-one trap: last, not first). Only complete
windows are emitted, so no padding is needed (EDA: every patient has >= 8 hours).
"""

from __future__ import annotations

import numpy as np
import torch
from torch.utils.data import Dataset

from .data import Patient

WINDOW = 8
STRIDE = 1


def make_windows(patients: list[Patient]) -> tuple[np.ndarray, np.ndarray]:
    """-> X (N, WINDOW, F) float32, y (N,) float32. Target = label at window's last hour."""
    xs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    for p in patients:
        T = p.feats.shape[0]
        if T < WINDOW:
            continue  # EDA guarantees T >= 8, but stay safe
        # sliding windows over the time axis
        for end in range(WINDOW, T + 1, STRIDE):
            start = end - WINDOW
            xs.append(p.feats[start:end])
            ys.append(p.labels[end - 1])  # last hour of the window
    if not xs:
        return np.empty((0, WINDOW, len(p.feats[0])), dtype="float32"), np.empty((0,), dtype="float32")
    X = np.stack(xs).astype("float32")
    y = np.asarray(ys, dtype="float32")
    return X, y


class WindowDataset(Dataset):
    def __init__(self, X: np.ndarray, y: np.ndarray):
        self.X = torch.from_numpy(X)
        self.y = torch.from_numpy(y)

    def __len__(self) -> int:
        return self.X.shape[0]

    def __getitem__(self, i: int):
        return self.X[i], self.y[i]
