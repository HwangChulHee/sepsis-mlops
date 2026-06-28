"""H4s-b — streaming simulator (결정 6).

Replays a patient's record ONE timestep at a time in chronological order, collecting the
per-timestep response. Strictly causal: step t is given rows[t] only (never t+1..), so a
prediction can never see the future. When the source is sealed setB it is REPLAY ONLY —
this module calls the predictor and NOTHING else (no fit/tune/select/normalize-recompute),
so B stays observation-only (H3 rule).
"""

from __future__ import annotations

from typing import Callable

import numpy as np


def replay(predict_fn: Callable[[np.ndarray], dict], rows: np.ndarray) -> list[dict]:
    """Feed rows[0..T-1] time-ordered, one at a time. predict_fn(row)->{p,alarm}.

    Causal by construction: at step t only rows[t] is passed (state holds 1..t-1).
    """
    rows = np.asarray(rows, dtype=np.float32)
    return [predict_fn(rows[t]) for t in range(rows.shape[0])]


def replay_patient(predictor, patient_id: str, rows: np.ndarray) -> list[dict]:
    """Convenience: replay through a StatefulPredictor for a given patient id."""
    return replay(lambda row: predictor.predict(patient_id, row), rows)
