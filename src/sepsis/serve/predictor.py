"""H4s-a — stateful GRU predictor (causal, per-patient state) (결정 3).

Carries per-patient hidden state and advances ONE timestep per call (O(1)/timestep),
numerically identical to re-feeding 1..t (unidirectional GRU). Per-patient lock
serializes same-patient requests so concurrent calls can't corrupt the read-modify-write
of hidden state. replicas=1 is assumed (in-memory state); a shared store (Redis) would be
needed for replicas>1 (handoff failure mode).
"""

from __future__ import annotations

import threading

import numpy as np
import torch

from sepsis.serve.bundle import Bundle
from sepsis.serve.preprocess_rt import StreamPreprocessor


class StatefulPredictor:
    def __init__(self, bundle: Bundle):
        self.b = bundle
        self.pre = StreamPreprocessor(bundle)
        self._h: dict[str, torch.Tensor] = {}      # pid -> (num_layers,1,hidden)
        self._locks: dict[str, threading.Lock] = {}
        self._registry = threading.Lock()          # guards _locks creation

    def _lock(self, pid: str) -> threading.Lock:
        with self._registry:
            lk = self._locks.get(pid)
            if lk is None:
                lk = self._locks[pid] = threading.Lock()
            return lk

    def reset(self, pid: str) -> None:
        with self._lock(pid):
            self._h.pop(pid, None)
            self.pre.reset(pid)

    def predict(self, pid: str, row: np.ndarray) -> dict:
        """One timestep for patient pid. row:(F,) NaN-for-missing -> {p, alarm}."""
        with self._lock(pid):                       # serialize same-patient (no torn state)
            z = self.pre.step(pid, row)             # (F,) normalized, frozen A stats
            x = torch.from_numpy(z).view(1, 1, self.b.input_dim)
            h = self._h.get(pid)
            with torch.no_grad():
                logit, h_n = self.b.model.forward_state(x, h)
            self._h[pid] = h_n                      # carry full h_n (all layers)
            p = float(torch.sigmoid(logit).reshape(-1)[-1].item())
            return {"p": p, "alarm": bool(p >= self.b.tau)}
