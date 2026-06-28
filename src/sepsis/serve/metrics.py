"""H4s-b — Prometheus metrics (결정 7). Foundation for H4-drift.

Exposes request count, latency, predicted-probability distribution, alarm rate, and
per-feature INPUT distributions (value histogram + missing counter) — the covariate-drift
foundation H4-drift builds on. /metrics serves these via prometheus_client.
"""

from __future__ import annotations

import numpy as np
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

PREDICT_REQUESTS = Counter("serve_predict_requests_total", "POST /predict calls")
HEALTH_REQUESTS = Counter("serve_health_requests_total", "GET /health calls")
ALARMS = Counter("serve_alarms_total", "predictions with p >= tau")
LATENCY = Histogram("serve_predict_latency_seconds", "per-/predict latency")
PRED_PROB = Histogram("serve_pred_prob", "predicted risk probability",
                      buckets=[i / 20 for i in range(21)])  # 0.0..1.0 step .05
# per-feature input distribution (covariate-drift foundation). Coarse shared buckets span
# vitals/labs/demographics scales (Gender 0/1 .. HR/BP ~hundreds); H4-drift can refine.
INPUT_FEATURE = Histogram("serve_input_feature_value", "observed input value per feature",
                          ["feature"],
                          buckets=[0, 1, 5, 10, 20, 30, 40, 50, 60, 80, 100, 120, 150, 200, 250, 300])
INPUT_MISSING = Counter("serve_input_missing_total", "missing (NaN) inputs per feature",
                        ["feature"])


def record(latency_s: float, p: float, alarm: bool, raw_row: np.ndarray, feature_names) -> None:
    PREDICT_REQUESTS.inc()
    LATENCY.observe(latency_s)
    PRED_PROB.observe(p)
    if alarm:
        ALARMS.inc()
    for name, v in zip(feature_names, raw_row):
        if np.isnan(v):
            INPUT_MISSING.labels(feature=name).inc()
        else:
            INPUT_FEATURE.labels(feature=name).observe(float(v))


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
