"""PhysioNet/CinC 2019 official utility score (H2-a, 결정 4).

Self-contained reimplementation of the official `evaluate_sepsis_score.py` utility,
inlined per h2_handoff.md H2-a (research/03 not needed). Constants/slopes verified
against physionetchallenges/evaluation-2019 `evaluate_sepsis_score.py`
(handoff review 16ba070).

Per-timestep utility U(s,t), summed per patient and across the cohort, then
normalized:  U_norm = (U_obs - U_inaction) / (U_best - U_inaction).

t_sepsis derivation (CRITICAL): the label turns on 6h before clinical onset, so
    t_sepsis = (first positive label index) + 6  = argmax(label) - dt_optimal.
Using the first positive index AS t_sepsis would shift every window by 6h and make
the whole evaluation wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

# --- official constants [확인됨: evaluate_sepsis_score.py] ---
DT_EARLY = -12      # reward window opens (hours relative to onset)
DT_OPTIMAL = -6     # maximum reward
DT_LATE = 3         # reward window closes
MAX_U_TP = 1.0
MIN_U_FN = -2.0
U_FP = -0.05
U_TN = 0.0

# --- derived affine pieces (official): u = m * offset + b, offset = t - t_sepsis ---
M1 = MAX_U_TP / (DT_OPTIMAL - DT_EARLY)        # +1/6  TP rise  (early -> optimal)
M2 = -MAX_U_TP / (DT_LATE - DT_OPTIMAL)        # -1/9  TP fall  (optimal -> late)
M3 = MIN_U_FN / (DT_LATE - DT_OPTIMAL)         # -2/9  FN       (optimal -> late)
B1 = -M1 * DT_EARLY                            # +2
B2 = -M2 * DT_LATE                             # +1/3
B3 = -M3 * DT_OPTIMAL                          # -4/3


def onset_index(labels: np.ndarray) -> int | None:
    """t_sepsis = first positive index + 6 (= argmax(label) - dt_optimal). None if non-septic."""
    labels = np.asarray(labels)
    if not labels.any():
        return None
    return int(np.argmax(labels)) - DT_OPTIMAL  # first_pos + 6


def utility_per_timestep(labels: np.ndarray, predictions: np.ndarray) -> np.ndarray:
    """Per-timestep utility contribution u[t] for one patient (float64).

    Septic patient (within [.., t_sepsis+dt_late]):
      predict 1 -> TP: rise max(M1*off+B1, U_FP) for off<=dt_optimal, else fall M2*off+B2
      predict 0 -> FN: 0 for off<=dt_optimal, else M3*off+B3
    After t_sepsis+dt_late: 0. Non-septic: predict 1 -> U_FP, predict 0 -> U_TN(0).
    """
    labels = np.asarray(labels)
    predictions = np.asarray(predictions)
    n = labels.shape[0]
    u = np.zeros(n, dtype=np.float64)

    t_sepsis = onset_index(labels)
    if t_sepsis is None:  # non-septic: FP penalty on any positive prediction
        u[predictions.astype(bool)] = U_FP
        return u

    for t in range(n):
        if t > t_sepsis + DT_LATE:
            continue  # window closed -> 0 regardless of prediction
        off = t - t_sepsis
        if predictions[t]:  # TP branch
            if t <= t_sepsis + DT_OPTIMAL:
                u[t] = max(M1 * off + B1, U_FP)   # rise, floor-clipped to U_FP (too early)
            else:
                u[t] = M2 * off + B2               # fall
        else:               # FN branch
            if t <= t_sepsis + DT_OPTIMAL:
                u[t] = 0.0
            else:
                u[t] = M3 * off + B3
    return u


def patient_utility(labels: np.ndarray, predictions: np.ndarray) -> float:
    """Summed utility for one patient."""
    return float(utility_per_timestep(labels, predictions).sum())


def inaction_predictions(labels: np.ndarray) -> np.ndarray:
    """All-negative prediction (the normalization's 0.0 baseline)."""
    return np.zeros(np.asarray(labels).shape[0], dtype=np.int8)


def best_predictions(labels: np.ndarray) -> np.ndarray:
    """Utility-optimal prediction. Septic: 1 over [max(0, t_sepsis+dt_early) :
    min(t_sepsis+dt_late+1, n)] (official range); else all 0."""
    labels = np.asarray(labels)
    n = labels.shape[0]
    best = np.zeros(n, dtype=np.int8)
    t_sepsis = onset_index(labels)
    if t_sepsis is not None:
        lo = max(0, t_sepsis + DT_EARLY)
        hi = min(t_sepsis + DT_LATE + 1, n)   # nit absorbed: official min(t_sepsis+4, n)
        if lo < hi:
            best[lo:hi] = 1
    return best


@dataclass
class UtilityResult:
    u_observed: float
    u_inaction: float
    u_best: float
    u_normalized: float


def normalized_utility(patients: Iterable[tuple[np.ndarray, np.ndarray]]) -> UtilityResult:
    """Cohort-level normalized utility.

    patients: iterable of (labels, predictions), predictions binary {0,1}.
    Normalization sums U_obs/U_inaction/U_best across the WHOLE cohort before the
    ratio (a single non-septic patient has U_best==U_inaction==0, so per-patient
    normalization would divide by zero; cohort-level does not).
    """
    u_obs = u_in = u_best = 0.0
    for labels, preds in patients:
        labels = np.asarray(labels)
        u_obs += patient_utility(labels, preds)
        u_in += patient_utility(labels, inaction_predictions(labels))
        u_best += patient_utility(labels, best_predictions(labels))
    denom = u_best - u_in
    u_norm = (u_obs - u_in) / denom if denom != 0 else float("nan")
    return UtilityResult(u_obs, u_in, u_best, u_norm)
