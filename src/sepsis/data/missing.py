"""H1-b — missing handling, model-branched (결정 2).

Tree path: NaN as-is (handled natively).
GRU path runtime order (결정 8): ① mask [from raw NaN, BEFORE ffill] ② ffill (past→future)
③ fill remaining NaN with TRAIN mean. zero-fill forbidden. Mask default OFF (결정 7).

Mask polarity: 1 = observed, 0 = missing.
"""

from __future__ import annotations

import numpy as np


def missing_mask(raw: np.ndarray) -> np.ndarray:
    """1 = observed, 0 = missing. Built from RAW NaN positions (must precede ffill)."""
    return (~np.isnan(raw)).astype(np.int8)


def ffill(a: np.ndarray) -> np.ndarray:
    """Forward-fill along time (axis 0), past→future only. Leading NaN stay NaN."""
    a = a.astype(np.float32, copy=True)
    n = a.shape[0]
    valid = ~np.isnan(a)
    # index of last valid row per column up to t; -1 if none yet
    row_idx = np.where(valid, np.arange(n)[:, None], -1)
    np.maximum.accumulate(row_idx, axis=0, out=row_idx)
    out = np.empty_like(a)
    col = np.arange(a.shape[1])[None, :]
    safe = np.where(row_idx < 0, 0, row_idx)
    out[:] = a[safe, col]
    out[row_idx < 0] = np.nan  # nothing to carry yet -> still NaN
    return out


def compute_fill_mean(train_ffilled: list[np.ndarray]) -> np.ndarray:
    """Per-feature nanmean over TRAIN patient-hours (after ffill). Imputation value."""
    stacked = np.concatenate(train_ffilled, axis=0)
    return np.nanmean(stacked, axis=0).astype(np.float32)


def fill_mean(a: np.ndarray, mean: np.ndarray) -> np.ndarray:
    """Fill remaining NaN with the train column mean. NOT zero-fill."""
    return np.where(np.isnan(a), mean[None, :], a).astype(np.float32)
