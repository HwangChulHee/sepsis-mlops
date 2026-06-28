"""H4d-a — distance metrics for drift (DDD 결정 3, handoff H4d-a).

Distance metrics ONLY (effect-size native; no KS p-value / analytic α / Bonferroni):
  numeric value  -> PSI (reference-quantile bins), Wasserstein reported
  categorical    -> Jensen-Shannon on category proportions
  missing rate   -> Jensen-Shannon on the observed/missing Bernoulli per feature
Reference and current MUST be the same per-patient-summary unit (asserted). Used by
synthetic.py for threshold calibration and as the H4d-b verification oracle.
"""

from __future__ import annotations

import numpy as np
from scipy.spatial.distance import jensenshannon
from scipy.stats import wasserstein_distance

from sepsis.drift.reference import Reference


def psi(ref: np.ndarray, cur: np.ndarray, n_bins: int = 10, eps: float = 1e-6) -> float:
    """Population Stability Index on reference-quantile bins (NaN dropped)."""
    ref = ref[~np.isnan(ref)]
    cur = cur[~np.isnan(cur)]
    if ref.size < n_bins or cur.size == 0:
        return float("nan")
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, n_bins + 1)))
    if edges.size < 3:                                  # near-constant -> use JS instead
        return js_values(ref, cur)
    edges[0], edges[-1] = -np.inf, np.inf
    r = np.histogram(ref, edges)[0] / ref.size + eps
    c = np.histogram(cur, edges)[0] / cur.size + eps
    return float(np.sum((c - r) * np.log(c / r)))


def js_values(ref: np.ndarray, cur: np.ndarray) -> float:
    """Jensen-Shannon distance over category proportions (categorical/low-cardinality)."""
    ref = ref[~np.isnan(ref)]
    cur = cur[~np.isnan(cur)]
    if ref.size == 0 or cur.size == 0:
        return float("nan")
    cats = np.unique(np.concatenate([ref, cur]))
    p = np.array([(ref == c).mean() for c in cats])
    q = np.array([(cur == c).mean() for c in cats])
    return float(jensenshannon(p, q, base=2))          # in [0,1]


def missing_js(ref_col: np.ndarray, cur_col: np.ndarray) -> float:
    """JS distance between observed/missing Bernoulli of reference vs current."""
    mr_ref = float(np.isnan(ref_col).mean())
    mr_cur = float(np.isnan(cur_col).mean())
    return float(jensenshannon([1 - mr_ref, mr_ref], [1 - mr_cur, mr_cur], base=2))


def wasserstein(ref: np.ndarray, cur: np.ndarray) -> float:
    ref = ref[~np.isnan(ref)]
    cur = cur[~np.isnan(cur)]
    if ref.size == 0 or cur.size == 0:
        return float("nan")
    return float(wasserstein_distance(ref, cur))


def assert_same_unit(ref: Reference, current: np.ndarray) -> None:
    """Reference and current must be the same per-patient-summary unit (else distance invalid)."""
    if ref.unit != "patient_last":
        raise ValueError(f"reference unit {ref.unit!r} != 'patient_last'")
    if current.ndim != 2 or current.shape[1] != len(ref.cols):
        raise ValueError(f"current shape {current.shape} incompatible with {len(ref.cols)} features")


def feature_distances(ref: Reference, current: np.ndarray) -> list[dict]:
    """Per-feature distances. current: (n, F) per-patient summary (same unit as ref)."""
    assert_same_unit(ref, current)
    out = []
    for j, name in enumerate(ref.cols):
        rcol, ccol = ref.summary[:, j], current[:, j]
        if ref.low_card[j]:
            value, metric = js_values(rcol, ccol), "js"
        else:
            value, metric = psi(rcol, ccol), "psi"
        out.append({"feature": name, "value": value, "metric": metric,
                    "wasserstein": wasserstein(rcol, ccol) if not ref.low_card[j] else float("nan"),
                    "missing_js": missing_js(rcol, ccol)})
    return out
