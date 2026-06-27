"""H1-b — GRU many-to-many sequences (결정 4).

Whole-patient sequence, per-timestep label. Variable lengths are batched with
RIGHT padding + a validity mask (1 = real timestep, 0 = padding). The GRU is
unidirectional/causal (config.GRU_BIDIRECTIONAL = False) so right padding never
leaks into a real-timestep prediction; the validity mask additionally excludes
padding from BOTH the training loss and the evaluation metrics.
"""

from __future__ import annotations

import numpy as np

from sepsis import config as C


def collate_m2m(batch: list[tuple[np.ndarray, np.ndarray]]):
    """batch: list of (feats T×F float32, labels T). -> right-padded arrays.

    Returns X (B,maxT,F), Y (B,maxT), validity (B,maxT) {1 real, 0 pad}, lengths (B,).
    """
    lengths = np.array([f.shape[0] for f, _ in batch], dtype=np.int64)
    maxT = int(lengths.max())
    F = batch[0][0].shape[1]
    B = len(batch)
    X = np.zeros((B, maxT, F), dtype=np.float32)
    Y = np.zeros((B, maxT), dtype=np.float32)
    validity = np.zeros((B, maxT), dtype=np.int8)
    for i, (f, lab) in enumerate(batch):
        T = f.shape[0]
        X[i, :T] = f
        Y[i, :T] = lab
        validity[i, :T] = 1  # right padding: real region is the prefix
    return X, Y, validity, lengths


def assert_causal() -> None:
    """Right-padding leak-freeness requires a unidirectional GRU."""
    assert C.GRU_BIDIRECTIONAL is False, "GRU must be unidirectional (causal) for right-pad safety"
