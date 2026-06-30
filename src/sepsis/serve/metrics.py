"""H4s-b — Prometheus metrics (결정 7). Foundation for H4-drift.

Exposes request count, latency, predicted-probability distribution, alarm rate, and
per-feature INPUT distributions (value histogram + missing counter) — the covariate-drift
foundation H4-drift builds on. /metrics serves these via prometheus_client.
"""

from __future__ import annotations

import os

import numpy as np
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

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
# 환자별 *최신* 위험도 — Grafana 가 patient_id 별 "위험도 선"을 그리게 한다(Histogram 으론 불가).
# ★ 무한 카디널리티 footgun: /predict 는 실트래픽 핫패스, PhysioNet 환자 40,336명. patient_id 를
# 라벨에 박으면 안 사라지는 시계열이 환자 수만큼 쌓인다. → 기본 OFF, SERVE_PER_PATIENT_GAUGE=1
# 일 때만 기록(리플레이/데모 전용). 프로젝트 '결측 마스크 기본 OFF(옵트인)' 철학과 동형
# (라운드 다 결정, design/replay/handoff_round_c.md §2). OFF 면 .labels() 를 안 불러 시계열 0개.
PRED_PROB_LATEST = Gauge("serve_pred_prob_latest",
                         "latest predicted risk per patient (OPT-IN — unbounded label)",
                         ["patient_id"])


def _per_patient_enabled() -> bool:
    """SERVE_PER_PATIENT_GAUGE 를 호출 시점에 동적 판독(import 시 상수화 금지 — 토글 가능)."""
    return os.environ.get("SERVE_PER_PATIENT_GAUGE", "").strip().lower() in ("1", "true", "yes", "on")


def record(latency_s: float, p: float, alarm: bool, raw_row: np.ndarray, feature_names,
           patient_id: str | None = None) -> None:
    PREDICT_REQUESTS.inc()
    LATENCY.observe(latency_s)
    PRED_PROB.observe(p)
    if alarm:
        ALARMS.inc()
    if patient_id is not None and _per_patient_enabled():   # 옵트인 + pid 있을 때만(카디널리티 가드)
        PRED_PROB_LATEST.labels(patient_id=patient_id).set(p)
    for name, v in zip(feature_names, raw_row):
        if np.isnan(v):
            INPUT_MISSING.labels(feature=name).inc()
        else:
            INPUT_FEATURE.labels(feature=name).observe(float(v))


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
