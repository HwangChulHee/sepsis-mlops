"""H4d-b — raw input window for drift (handoff H4d-b).

Collects (patient_id, raw_row) from serving and aggregates to the per-patient-summary
unit (last observed value per feature = same unit as the H4d-a reference). This is a
SEPARATE store from the serving predictor's per-patient hidden state (no contamination).
Light deps only (numpy + missing) so importing it in the serving path adds no weight.
"""

from __future__ import annotations

import threading
from collections import OrderedDict, deque

import numpy as np

from sepsis.data import missing


class DriftWindow:
    """Thread-safe: FastAPI runs sync path handlers in a threadpool, so /predict (add) and
    /drift (patient_summary·n_patients) execute concurrently. Iterating a deque while another
    thread appends raises `RuntimeError: deque mutated during iteration`. A lock guards every
    buffer touch; reads take a cheap reference-snapshot under the lock and do the heavy
    vstack/ffill OUTSIDE it (short hold time)."""

    def __init__(self, maxlen: int = 5000):
        self._buf: deque = deque(maxlen=maxlen)   # (pid, raw_row float32 (F,))
        self._lock = threading.Lock()

    def add(self, patient_id: str, raw_row: np.ndarray) -> None:
        # asarray/copy BEFORE the lock so the hold is just the append.
        item = (str(patient_id), np.asarray(raw_row, dtype=np.float32).copy())
        with self._lock:
            self._buf.append(item)

    def _snapshot(self) -> list:
        with self._lock:
            return list(self._buf)   # copies the tuple refs only (no deep copy) — cheap

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)

    def patient_ids(self) -> set[str]:
        return {pid for pid, _ in self._snapshot()}

    def n_patients(self) -> int:
        return len(self.patient_ids())

    def ready(self, min_patients: int) -> bool:
        return self.n_patients() >= min_patients

    def patient_summary(self) -> np.ndarray:
        """(n_patients, F): per-patient last observed value (ffill end-state) — reference unit."""
        groups: OrderedDict[str, list] = OrderedDict()
        for pid, row in self._snapshot():
            groups.setdefault(pid, []).append(row)
        rows = [missing.ffill(np.vstack(rs))[-1] for rs in groups.values()]
        if not rows:
            return np.empty((0, 0), dtype=np.float32)
        return np.vstack(rows).astype(np.float32)


_WINDOW: DriftWindow | None = None


def get_window(maxlen: int = 5000) -> DriftWindow:
    global _WINDOW
    if _WINDOW is None:
        _WINDOW = DriftWindow(maxlen=maxlen)
    return _WINDOW


def reset_window() -> None:
    global _WINDOW
    _WINDOW = None
