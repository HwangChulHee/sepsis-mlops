"""H3-b step 1 — equivalence vs the official PhysioNet/CinC 2019 scorer (결정 4).

`_official_compute_prediction_utility` is VENDORED VERBATIM from
physionetchallenges/evaluation-2019 `evaluate_sepsis_score.py`
(`compute_prediction_utility`), and `_official_normalized` reproduces that repo's
cohort normalization (best/inaction construction + ratio). We then check our
`eval/utility.py` is bit-equal to it (±tol) on a cohort of edge cases. B is NOT used.
"""

from __future__ import annotations

import numpy as np

from sepsis.eval import utility as U


# --------------------------------------------------------------------------
# VERBATIM from official evaluate_sepsis_score.py (do not edit — reference oracle)
# --------------------------------------------------------------------------
def _official_compute_prediction_utility(labels, predictions, dt_early=-12, dt_optimal=-6,
                                         dt_late=3.0, max_u_tp=1, min_u_fn=-2, u_fp=-0.05,
                                         u_tn=0, check_errors=True):
    if check_errors:
        if len(predictions) != len(labels):
            raise Exception('Numbers of predictions and labels must be the same.')
        for label in labels:
            if label not in (0, 1):
                raise Exception('Labels must satisfy label == 0 or label == 1.')
        for prediction in predictions:
            if prediction not in (0, 1):
                raise Exception('Predictions must satisfy prediction == 0 or prediction == 1.')
        if dt_early >= dt_optimal:
            raise Exception('The earliest beneficial time for predictions must be before the optimal time.')
        if dt_optimal >= dt_late:
            raise Exception('The optimal time for predictions must be before the latest beneficial time.')

    if np.any(labels):
        is_septic = True
        t_sepsis = np.argmax(labels) - dt_optimal
    else:
        is_septic = False
        t_sepsis = float('inf')

    n = len(labels)
    m_1 = float(max_u_tp) / float(dt_optimal - dt_early)
    b_1 = -m_1 * dt_early
    m_2 = float(-max_u_tp) / float(dt_late - dt_optimal)
    b_2 = -m_2 * dt_late
    m_3 = float(min_u_fn) / float(dt_late - dt_optimal)
    b_3 = -m_3 * dt_optimal

    u = np.zeros(n)
    for t in range(n):
        if t <= t_sepsis + dt_late:
            if is_septic and predictions[t]:
                if t <= t_sepsis + dt_optimal:
                    u[t] = max(m_1 * (t - t_sepsis) + b_1, u_fp)
                elif t <= t_sepsis + dt_late:
                    u[t] = m_2 * (t - t_sepsis) + b_2
            elif not is_septic and predictions[t]:
                u[t] = u_fp
            elif is_septic and not predictions[t]:
                if t <= t_sepsis + dt_optimal:
                    u[t] = 0
                elif t <= t_sepsis + dt_late:
                    u[t] = m_3 * (t - t_sepsis) + b_3
            elif not is_septic and not predictions[t]:
                u[t] = u_tn
    return np.sum(u)


def _official_best_inaction(labels, dt_early=-12, dt_optimal=-6, dt_late=3):
    """best/inaction predictions exactly as the official driver builds them."""
    n = len(labels)
    inaction = np.zeros(n)
    best = np.zeros(n)
    if np.any(labels):
        t_sepsis = int(np.argmax(labels)) - dt_optimal
        best[max(0, t_sepsis + dt_early):min(t_sepsis + dt_late + 1, n)] = 1
    return best, inaction


def _official_normalized(cohort):
    """Cohort normalized utility, official formula. cohort: [(labels, predictions)]."""
    obs = best = inact = 0.0
    for labels, preds in cohort:
        labels = np.asarray(labels)
        b, ia = _official_best_inaction(labels)
        obs += _official_compute_prediction_utility(labels, np.asarray(preds))
        best += _official_compute_prediction_utility(labels, b)
        inact += _official_compute_prediction_utility(labels, ia)
    return (obs - inact) / (best - inact)


# --------------------------------------------------------------------------
# equivalence: ours vs official
# --------------------------------------------------------------------------
def check_equivalence(cohort, tol: float = 1e-6):
    """Compare our utility to the official oracle on `cohort`.

    Returns (ok, max_abs_diff, n_checks, details). cohort: [(labels, binary_preds)].
    Per-patient utility, per-patient best/inaction, and cohort normalized are all checked.
    """
    max_diff = 0.0
    n_checks = 0
    details = []

    # per-patient observed / best / inaction
    for i, (labels, preds) in enumerate(cohort):
        labels = np.asarray(labels)
        preds = np.asarray(preds)
        off = _official_compute_prediction_utility(labels, preds)
        our = U.patient_utility(labels, preds)
        d = abs(off - our)
        max_diff = max(max_diff, d); n_checks += 1
        if d > tol:
            details.append(f"patient {i} observed: official={off:.8f} ours={our:.8f} Δ={d:.2e}")

        ob, oia = _official_best_inaction(labels)
        d_best = abs(_official_compute_prediction_utility(labels, ob)
                     - U.patient_utility(labels, U.best_predictions(labels)))
        d_ia = abs(_official_compute_prediction_utility(labels, oia)
                   - U.patient_utility(labels, U.inaction_predictions(labels)))
        max_diff = max(max_diff, d_best, d_ia); n_checks += 2
        if d_best > tol:
            details.append(f"patient {i} best: Δ={d_best:.2e}")
        if d_ia > tol:
            details.append(f"patient {i} inaction: Δ={d_ia:.2e}")

    # cohort normalized
    off_norm = _official_normalized(cohort)
    our_norm = U.normalized_utility(cohort).u_normalized
    d_norm = abs(off_norm - our_norm)
    max_diff = max(max_diff, d_norm); n_checks += 1
    if d_norm > tol:
        details.append(f"cohort normalized: official={off_norm:.8f} ours={our_norm:.8f} Δ={d_norm:.2e}")

    ok = max_diff <= tol
    return ok, max_diff, n_checks, details
