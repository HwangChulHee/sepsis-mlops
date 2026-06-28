"""H2-b/c — A-val threshold (τ) selection (결정 4).

τ converts probabilities to binary predictions so the official utility (eval/utility.py)
can be scored. τ is chosen on A-val to MAXIMIZE normalized utility, then frozen. H3
applies the frozen τ to B (never re-tuned on B — leakage).

★ τ is per (model × featureset): it is an evaluation post-processing knob, not a
learning variable, so each featureset re-selects its own τ (HP is shared, τ is not).

Vectorized: per-timestep TP/FN contributions are τ-independent, so the τ grid loop is
just a masked sum. Cohort-level normalization (sum over all patients before the ratio).
"""

from __future__ import annotations

import numpy as np

from sepsis.eval import utility as U


def _stack(val_labels, val_probs):
    tp, fn, best, probs = [], [], [], []
    for lab, pr in zip(val_labels, val_probs):
        lab = np.asarray(lab)
        tp.append(U.utility_per_timestep(lab, np.ones(lab.shape[0], np.int8)))
        fn.append(U.utility_per_timestep(lab, np.zeros(lab.shape[0], np.int8)))
        best.append(U.best_predictions(lab).astype(bool))
        probs.append(np.asarray(pr, dtype=np.float64))
    return (np.concatenate(tp), np.concatenate(fn),
            np.concatenate(best), np.concatenate(probs))


def select_threshold(val_labels, val_probs, n_grid: int = 257) -> tuple[float, float]:
    """Return (tau*, U_norm@tau*). val_labels/val_probs: per-patient arrays.

    tau* maximizes cohort normalized utility on A-val.
    """
    tp, fn, best, probs = _stack(val_labels, val_probs)
    u_in = float(fn.sum())                          # all-negative baseline
    u_best = float(np.where(best, tp, fn).sum())    # utility-optimal prediction
    denom = u_best - u_in
    if denom == 0:
        return 0.5, float("nan")

    # candidate thresholds: quantiles of the prob distribution + endpoints
    grid = np.unique(np.concatenate([
        [0.0], np.quantile(probs, np.linspace(0.0, 1.0, n_grid)), [1.0 + 1e-9]]))
    best_tau, best_norm = 0.5, -np.inf
    for tau in grid:
        u_obs = float(np.where(probs >= tau, tp, fn).sum())
        norm = (u_obs - u_in) / denom
        if norm > best_norm:
            best_norm, best_tau = norm, float(tau)
    return best_tau, best_norm


def utility_at(val_labels, val_probs, tau: float) -> float:
    """Cohort normalized utility at a FIXED tau (for applying a frozen threshold)."""
    tp, fn, best, probs = _stack(val_labels, val_probs)
    u_in = float(fn.sum())
    u_best = float(np.where(best, tp, fn).sum())
    denom = u_best - u_in
    if denom == 0:
        return float("nan")
    u_obs = float(np.where(probs >= tau, tp, fn).sum())
    return (u_obs - u_in) / denom
