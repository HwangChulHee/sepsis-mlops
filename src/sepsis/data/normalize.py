"""H1-b — clipping + train-only z-score (결정 3).

Order: clip (fixed physiological bounds, DATA-INDEPENDENT) BEFORE z-score, so the
normalization stats are not swayed by extremes. μ/σ computed from the TRAIN split
only (A-train in cross_site).
"""

from __future__ import annotations

import numpy as np

from sepsis import config as C


def clip_bounds(featureset: str) -> tuple[np.ndarray, np.ndarray]:
    cols = C.featureset_columns(featureset)
    lo = np.array([C.CLIP_BOUNDS[c][0] for c in cols], dtype=np.float32)
    hi = np.array([C.CLIP_BOUNDS[c][1] for c in cols], dtype=np.float32)
    return lo, hi


def clip(a: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    return np.clip(a, lo[None, :], hi[None, :]).astype(np.float32)


def compute_norm_stats(train_arrays: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Plain mean/std over TRAIN patient-hours (post fill + clip). Guards constant cols.

    Accumulate in float64 (stable over ~1e6 rows), return float32 for the model.
    """
    stacked = np.concatenate(train_arrays, axis=0).astype(np.float64)
    mean = stacked.mean(axis=0)
    std = stacked.std(axis=0)
    std = np.where(std < 1e-8, 1.0, std)
    return mean.astype(np.float32), std.astype(np.float32)


def normalize(a: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    return ((a - mean[None, :]) / std[None, :]).astype(np.float32)
