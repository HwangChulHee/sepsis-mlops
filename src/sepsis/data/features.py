"""H1-b — tree per-timestep lookback summary (결정 6).

Each timestep t -> one row of summary statistics over the lookback window
[t-LOOKBACK+1 .. t] (<= 8h), aligned with the GRU's per-timestep prediction so the
two models share evaluation units. Early timesteps (t < LOOKBACK-1) use only the
available range (not dropped -> aligns with GRU). NaN as-is (tree-native); all
statistics are NaN-aware; an all-NaN window yields NaN.

Stats per feature (결정 6): last, mean, min, max, delta(last-first), range(max-min), variance.
"""

from __future__ import annotations

import warnings

import numpy as np

from sepsis import config as C


def _windows(feats: np.ndarray, window: int) -> np.ndarray:
    """(T,F) -> (T,window,F): row t = feats[t-window+1 .. t], left-padded with NaN."""
    T, F = feats.shape
    pad = np.full((window - 1, F), np.nan, dtype=np.float32)
    padded = np.vstack([pad, feats])
    idx = np.arange(T)[:, None] + np.arange(window)[None, :]  # (T,window)
    return padded[idx]  # (T,window,F)


def lookback_summary(feats: np.ndarray, window: int = C.LOOKBACK) -> np.ndarray:
    """(T,F) -> (T, F*7) NaN-aware lookback summary. One row per timestep (no drop)."""
    T, F = feats.shape
    W = _windows(feats, window)            # (T,window,F)
    maskW = ~np.isnan(W)
    count = maskW.sum(axis=1)              # (T,F)

    with warnings.catch_warnings():        # all-NaN slices -> NaN (expected)
        warnings.simplefilter("ignore", RuntimeWarning)
        mean = np.nanmean(W, axis=1)
        mn = np.nanmin(W, axis=1)
        mx = np.nanmax(W, axis=1)
        var = np.nanvar(W, axis=1)

    last = feats.astype(np.float32, copy=True)  # value at t (may be NaN)
    rng = mx - mn

    # delta = last_observed - first_observed in window (NaN if < 2 observed)
    ar = np.arange(window)
    first_idx = np.where(maskW, ar[None, :, None], window).min(axis=1)   # (T,F)
    last_idx = np.where(maskW, ar[None, :, None], -1).max(axis=1)        # (T,F)
    ti = np.arange(T)[:, None]
    fi = np.arange(F)[None, :]
    first_val = np.where(first_idx < window, W[ti, np.clip(first_idx, 0, window - 1), fi], np.nan)
    last_val = np.where(last_idx >= 0, W[ti, np.clip(last_idx, 0, window - 1), fi], np.nan)
    delta = np.where(count >= 2, last_val - first_val, np.nan)

    # block layout: [last(F) | mean(F) | min(F) | max(F) | delta(F) | range(F) | var(F)]
    out = np.concatenate([last, mean, mn, mx, delta, rng, var], axis=1)
    return out.astype(np.float32)


def summary_columns(featureset: str) -> list[str]:
    cols = C.featureset_columns(featureset)
    return [f"{c}__{stat}" for stat in C.TREE_STATS for c in cols]
