"""H4 drift loop — analyze the live serving window vs the ACTIVE bundle reference.

Closes the observe step of the MLOps loop: the serving process loads its baseline FROM ITS
ACTIVE BUNDLE (reference.npz — so it rolls back together with the model, see retrain.deploy)
and runs the numpy-only distance test (synthetic.calibrate/detect via distance.py, NOT
Evidently → serving stays lean) on the accumulated per-patient window, then publishes
observational gauges. Watch-only: promotion to an action lives in retrain.promote
(drift-driven, human-in-the-loop). The detection dict shape matches promote.promote's input
(dataset_drift_share + features[].drift) and watch.publish's expectations.
"""

from __future__ import annotations

import numpy as np

from sepsis.drift import synthetic, watch


def to_detection(per_feature: list[dict]) -> dict:
    """Wrap synthetic.detect's per-feature list into the detector/watch/promote dict shape."""
    share = float(np.mean([f["drift"] for f in per_feature])) if per_feature else 0.0
    return {"features": per_feature, "dataset_drift_share": share,
            "methods": sorted({f.get("metric", "") for f in per_feature})}


def analyze(reference, window, thresholds, *, min_patients: int | None = None) -> dict | None:
    """Detect drift on `window` vs `reference`; publish gauges; return the detection (or None
    if the window is too small — insufficient is published, no test run)."""
    mp = min_patients if min_patients is not None else thresholds.window_n
    if not window.ready(mp):
        watch.publish_insufficient()
        return None
    per_feature = synthetic.detect(reference, window.patient_summary(), thresholds)
    det = to_detection(per_feature)
    watch.publish(det)
    return det
