"""H4r-a — delayed-label backfill (handoff H4r-a, DDD 결정 3).

Labels arrive LATE (confirmed retrospectively), so performance can only be measured with
a lag. Here the labels are REAL (setB SepsisLabel) — only the ARRIVAL TIMING is simulated
(a patient's outcome is "confirmed" `delay` after stay end). At analysis time `now`, only
already-arrived labels are scored -> an AUXILIARY performance estimate that fills in over
time.

★ Umbrella-seller (feedback-loop) bias is UNTAGGED: PhysioNet has no intervention/treatment
column, so model-success-then-clinician-intervention cases that flip the label to negative
cannot be identified. Therefore performance here is AUXILIARY ONLY and is NOT a retrain
trigger (drift is — see promote.py); this bias is surfaced, not corrected.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sklearn.metrics import average_precision_score

from sepsis.eval import threshold


def simulate_arrivals(stay_end_times: list[float], delay: float) -> np.ndarray:
    """Per-patient label arrival time = stay_end + delay (real label, simulated lag)."""
    return np.asarray(stay_end_times, dtype=np.float64) + float(delay)


def available_at(arrivals: np.ndarray, now: float) -> np.ndarray:
    """Boolean mask of patients whose (real) label has arrived by `now`."""
    return np.asarray(arrivals) <= now


@dataclass
class BackfillResult:
    now: float
    n_available: int
    n_total: int
    utility: float                      # auxiliary, on arrived labels only
    prauc: float
    performance_is_auxiliary: bool = True
    umbrella_seller_untagged: bool = True
    note: str = field(default="performance auxiliary (drift is primary trigger); "
                              "umbrella-seller bias untagged (no intervention column)")


def backfill_performance(per_patient_labels: list[np.ndarray],
                         per_patient_probs: list[np.ndarray],
                         tau: float, arrivals: np.ndarray, now: float) -> BackfillResult:
    """Auxiliary performance on labels that have ARRIVED by `now` (real labels, lagged)."""
    mask = available_at(arrivals, now)
    idx = np.flatnonzero(mask)
    labels_av = [per_patient_labels[i] for i in idx]
    probs_av = [per_patient_probs[i] for i in idx]
    if labels_av:
        util = threshold.utility_at(labels_av, probs_av, tau)
        y = np.concatenate([np.asarray(l) for l in labels_av])
        p = np.concatenate([np.asarray(pr) for pr in probs_av])
        prauc = float(average_precision_score(y, p)) if y.max() > 0 else float("nan")
    else:
        util, prauc = float("nan"), float("nan")
    return BackfillResult(now=float(now), n_available=len(idx),
                          n_total=len(per_patient_labels), utility=util, prauc=prauc)
