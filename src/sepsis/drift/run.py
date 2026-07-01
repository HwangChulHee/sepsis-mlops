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
    # 검출 표본수를 보정 표본수(window_n)에 맞춘다. 임계값은 window_n 표본의 H0 분산으로
    # 잡혔는데(synthetic.calibrate: bootstrap(ref.summary, window_n)), 더 큰 표본에 그대로
    # 적용하면 H0 분산이 작아져 임계가 과대해지고 실제 드리프트를 과소검출한다. 슬라이딩
    # 윈도우 의미대로 가장 최근 window_n 환자만 비교한다(가용 환자 < window_n 이면 게이트에서 이미 return).
    summary = window.patient_summary()
    n_cal = thresholds.window_n
    if summary.shape[0] > n_cal:
        summary = summary[-n_cal:]
    per_feature = synthetic.detect(reference, summary, thresholds)
    det = to_detection(per_feature)
    watch.publish(det)
    return det
