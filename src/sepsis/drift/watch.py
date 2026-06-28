"""H4d-b — watch signal to Prometheus (handoff H4d-b, DDD 결정 5 경계).

OBSERVATION ONLY: exposes per-feature drift distance, missing-JS, watch state, dataset
drift share, and insufficient-data. There is intentionally NO alarm / action / promotion /
retrain / label here — those belong to H4-재학습. watch = "drift observed", not an alarm.
"""

from __future__ import annotations

import numpy as np
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest

DRIFT_DISTANCE = Gauge("drift_value_distance", "per-feature value drift distance", ["feature"])
DRIFT_MISSING = Gauge("drift_missing_js", "per-feature missing-rate JS distance", ["feature"])
DRIFT_STATE = Gauge("drift_state", "per-feature watch state (1=drift observed)", ["feature"])
DATASET_DRIFT_SHARE = Gauge("drift_dataset_share", "fraction of features in watch state")
INSUFFICIENT = Gauge("drift_insufficient_data", "1 if window has too few patients to test")


def _g(x: float) -> float:
    return float(x) if np.isfinite(x) else 0.0


def publish(detection: dict) -> None:
    """Set observational gauges from detector.detect output. No alarm/action/promotion."""
    for f in detection["features"]:
        DRIFT_DISTANCE.labels(feature=f["feature"]).set(_g(f["value"]))
        DRIFT_MISSING.labels(feature=f["feature"]).set(_g(f["missing_js"]))
        DRIFT_STATE.labels(feature=f["feature"]).set(1.0 if f["drift"] else 0.0)
    DATASET_DRIFT_SHARE.set(_g(detection["dataset_drift_share"]))
    INSUFFICIENT.set(0.0)


def publish_insufficient() -> None:
    """Window too small — record insufficient-data, do not test."""
    INSUFFICIENT.set(1.0)


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
