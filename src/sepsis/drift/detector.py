"""H4d-b — Evidently drift detector, distance method PINNED (handoff H4d-b, DDD 결정 3·7).

Operational engine = Evidently. `num_stattest`/`cat_stattest` are pinned to DISTANCE
metrics (PSI numeric, Jensen-Shannon categorical) so Evidently never auto-falls-back to
KS for small (n<=1000) medical windows. Thresholds are calibrated empirically on the SAME
engine (bootstrap H0 -> (1-alpha) quantile), matching the H4d-a method. Reference/current
are the SAME per-patient-summary unit (asserted via reference). Missing-rate drift is
carried as a side signal (JS) since value-drift runs on observed values.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from sepsis.drift import distance as D
from sepsis.drift.reference import Reference

NUM_METHOD = "psi"
CAT_METHOD = "jensenshannon"


def _frame(reference: Reference, mat: np.ndarray) -> pd.DataFrame:
    D.assert_same_unit(reference, mat)
    return pd.DataFrame(mat, columns=reference.cols)


def _data_definition(reference: Reference):
    from evidently import DataDefinition
    num = [c for c, lc in zip(reference.cols, reference.low_card) if not lc]
    cat = [c for c, lc in zip(reference.cols, reference.low_card) if lc]
    return DataDefinition(numerical_columns=num, categorical_columns=cat)


def evidently_distances(reference: Reference, current: np.ndarray) -> dict[str, dict]:
    """Per-column Evidently drift distance with method PINNED (never KS)."""
    from evidently import Dataset, Report
    from evidently.metrics import ValueDrift

    dd = _data_definition(reference)
    ref_ds = Dataset.from_pandas(_frame(reference, reference.summary), data_definition=dd)
    cur_ds = Dataset.from_pandas(_frame(reference, current), data_definition=dd)
    metrics = [ValueDrift(column=c, method=(CAT_METHOD if lc else NUM_METHOD))
               for c, lc in zip(reference.cols, reference.low_card)]
    res = Report(metrics).run(reference_data=ref_ds, current_data=cur_ds).dict()
    out = {}
    for m in res["metrics"]:
        cfg = m["config"]
        out[cfg["column"]] = {"value": float(m["value"]), "method": cfg["method"]}
    return out


@dataclass
class EviThresholds:
    alpha: float
    window_n: int
    value: dict[str, float]
    missing: dict[str, float]


def calibrate(reference: Reference, *, alpha: float = 0.05, window_n: int = 500,
              n_trials: int = 40, seed: int = 0) -> EviThresholds:
    """Bootstrap H0 on the EVIDENTLY engine -> per-column (1-alpha) quantile thresholds."""
    rng = np.random.default_rng(seed)
    vals = {c: [] for c in reference.cols}
    miss = {c: [] for c in reference.cols}
    n = reference.summary.shape[0]
    for _ in range(n_trials):
        cur = reference.summary[rng.integers(0, n, size=window_n)]
        ed = evidently_distances(reference, cur)
        for j, c in enumerate(reference.cols):
            vals[c].append(ed[c]["value"])
            miss[c].append(D.missing_js(reference.summary[:, j], cur[:, j]))
    q = 1.0 - alpha
    return EviThresholds(alpha=alpha, window_n=window_n,
                         value={c: float(np.nanquantile(vals[c], q)) for c in reference.cols},
                         missing={c: float(np.nanquantile(miss[c], q)) for c in reference.cols})


def detect(reference: Reference, current: np.ndarray, thr: EviThresholds) -> dict:
    """Per-feature drift flag (value distance OR missing-JS exceeds threshold). watch-only."""
    ed = evidently_distances(reference, current)
    feats = []
    for j, c in enumerate(reference.cols):
        val = ed[c]["value"]
        mjs = D.missing_js(reference.summary[:, j], current[:, j])
        vflag = np.isfinite(val) and val > thr.value[c]
        mflag = np.isfinite(mjs) and mjs > thr.missing[c]
        feats.append({"feature": c, "value": val, "method": ed[c]["method"],
                      "value_thr": thr.value[c], "missing_js": mjs, "missing_thr": thr.missing[c],
                      "value_drift": bool(vflag), "missing_drift": bool(mflag),
                      "drift": bool(vflag or mflag)})
    share = float(np.mean([f["drift"] for f in feats]))
    return {"features": feats, "dataset_drift_share": share,
            "methods": sorted({f["method"] for f in feats})}
