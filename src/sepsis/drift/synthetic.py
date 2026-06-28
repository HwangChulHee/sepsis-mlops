"""H4d-a — synthetic drift injection + empirical threshold calibration (DDD 결정 4).

False-alarm control is EMPIRICAL (no analytic α): bootstrap the reference under H0 (no
drift) to set per-feature distance thresholds at the (1-α) quantile, so a fresh H0 batch
false-alarms at ≈α. Injected shifts (mean shift / missing-rate increase) must then exceed
those thresholds. This calibrates the rule-of-thumb PSI 0.1/0.2 to our data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from sepsis.drift import distance as D
from sepsis.drift.reference import Reference


# ---------------- injection / resampling ----------------
def bootstrap(summary: np.ndarray, n: int, rng: np.random.Generator) -> np.ndarray:
    return summary[rng.integers(0, summary.shape[0], size=n)].copy()


def inject_mean_shift(summary: np.ndarray, j: int, delta: float) -> np.ndarray:
    out = summary.copy()
    obs = ~np.isnan(out[:, j])
    out[obs, j] = out[obs, j] + delta
    return out


def inject_missing_increase(summary: np.ndarray, j: int, extra_rate: float,
                            rng: np.random.Generator) -> np.ndarray:
    out = summary.copy()
    obs_idx = np.flatnonzero(~np.isnan(out[:, j]))
    k = int(round(len(obs_idx) * extra_rate))
    if k:
        out[rng.choice(obs_idx, size=k, replace=False), j] = np.nan
    return out


# ---------------- empirical calibration ----------------
@dataclass
class Thresholds:
    alpha: float
    window_n: int
    value: np.ndarray           # (F,) per-feature value-distance threshold
    missing: np.ndarray         # (F,) per-feature missing-JS threshold
    cols: list[str] = field(default_factory=list)


def _batch_distances(ref: Reference, current: np.ndarray):
    fd = D.feature_distances(ref, current)
    return (np.array([f["value"] for f in fd]), np.array([f["missing_js"] for f in fd]))


def calibrate(ref: Reference, *, alpha: float = 0.05, window_n: int = 500,
              n_trials: int = 300, seed: int = 0) -> Thresholds:
    """Set per-feature thresholds at the (1-α) quantile of H0 (bootstrap) distances."""
    rng = np.random.default_rng(seed)
    vals, miss = [], []
    for _ in range(n_trials):
        cur = bootstrap(ref.summary, window_n, rng)
        v, m = _batch_distances(ref, cur)
        vals.append(v); miss.append(m)
    vals, miss = np.array(vals), np.array(miss)          # (n_trials, F)
    q = 1.0 - alpha
    value_thr = np.nanquantile(vals, q, axis=0)
    missing_thr = np.nanquantile(miss, q, axis=0)
    return Thresholds(alpha=alpha, window_n=window_n, value=value_thr,
                      missing=missing_thr, cols=ref.cols)


def detect(ref: Reference, current: np.ndarray, thr: Thresholds) -> list[dict]:
    """Per-feature drift flag = value-distance OR missing-JS exceeds its threshold."""
    fd = D.feature_distances(ref, current)
    out = []
    for j, f in enumerate(fd):
        vflag = np.isfinite(f["value"]) and f["value"] > thr.value[j]
        mflag = np.isfinite(f["missing_js"]) and f["missing_js"] > thr.missing[j]
        out.append({**f, "value_thr": float(thr.value[j]), "missing_thr": float(thr.missing[j]),
                    "drift": bool(vflag or mflag), "value_drift": bool(vflag),
                    "missing_drift": bool(mflag)})
    return out


def false_alarm_rate(ref: Reference, thr: Thresholds, *, n_trials: int = 300,
                     seed: int = 1) -> dict:
    """Fresh H0 (no injected drift): fraction of feature-comparisons flagged. Should ≈ α."""
    rng = np.random.default_rng(seed)
    v_flags, m_flags, any_flags = 0, 0, 0
    total = 0
    for _ in range(n_trials):
        cur = bootstrap(ref.summary, thr.window_n, rng)
        for f in detect(ref, cur, thr):
            total += 1
            v_flags += f["value_drift"]
            m_flags += f["missing_drift"]
            any_flags += f["drift"]
    return {"value_fpr": v_flags / total, "missing_fpr": m_flags / total,
            "any_fpr": any_flags / total, "n_comparisons": total}
