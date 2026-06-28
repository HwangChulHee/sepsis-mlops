"""H4s-a — streaming preprocess, bit-identical to the training pipeline (결정 4).

Per-patient ffill state = last-observed value per feature, initialized to NaN (NOT 0 /
mean — that would be skew). Each timestep: ffill(carry) -> fill(A-train mean) ->
clip(A range) -> z-score(A μ/σ), mask OFF. fill/clip/z-score reuse the EXACT training
apply-functions (missing.fill_mean, normalize.clip/normalize) with the bundle's frozen
A constants, so the only streaming-specific logic is the ffill carry. No fitting here.
"""

from __future__ import annotations

import numpy as np

from sepsis.data import missing, normalize
from sepsis.serve.bundle import Bundle


class StreamPreprocessor:
    """Stateful per-patient streaming preprocess. Mask OFF (input stays F-dim)."""

    def __init__(self, bundle: Bundle):
        self.b = bundle
        self.F = bundle.input_dim
        self._last: dict[str, np.ndarray] = {}   # pid -> last-observed (ffill state)

    def reset(self, pid: str) -> None:
        self._last.pop(pid, None)

    def step(self, pid: str, row: np.ndarray) -> np.ndarray:
        """row: (F,) float with np.nan for unmeasured features. -> (F,) normalized."""
        row = np.asarray(row, dtype=np.float32)
        if row.shape != (self.F,):
            raise ValueError(f"row shape {row.shape} != ({self.F},)")

        # ffill carry: state starts as NaN, updated only where observed (leading NaN stay NaN)
        state = self._last.get(pid)
        if state is None:
            state = np.full(self.F, np.nan, dtype=np.float32)
        else:
            state = state.copy()
        obs = ~np.isnan(row)
        state[obs] = row[obs]
        self._last[pid] = state

        # fill -> clip -> z-score via the SAME training apply-functions (bit-identical)
        a = state[None, :]                                   # (1,F)
        a = missing.fill_mean(a, self.b.fill_mean)           # NaN -> A-train mean (not 0)
        a = normalize.clip(a, self.b.clip_lo, self.b.clip_hi)
        a = normalize.normalize(a, self.b.mu, self.b.sigma)
        return a[0]
