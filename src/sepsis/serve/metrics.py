"""H4s-b — Prometheus metrics (결정 7). Foundation for H4-drift.

Exposes request count, latency, predicted-probability distribution, alarm rate, and
per-feature INPUT distributions (value histogram + missing counter) — the covariate-drift
foundation H4-drift builds on. /metrics serves these via prometheus_client.

★ serving-benchmark 2A — 관측성 env 게이트 (NB3): 매 요청의 **부가 계측**(피처별 입력
분포 히스토그램/결측 카운터)은 `SEPSIS_SERVE_AUX_METRICS` 로 켜고 끌 수 있다(기본 ON).
게이트 OFF(arm-2 순수 추론 프로파일)여도 `serve_predict_latency_seconds`·요청 카운터 등
**예측/추론 관측은 불변**이다 — 게이트는 "무엇을 관측하는가"만 바꾼다.

★ 인스턴스 격리: 벤치가 여러 서버를 한 프로세스/테스트에서 띄울 때 부가계측 시계열이
서로 새지 않도록 `MetricSet(registry=...)` 로 **인스턴스별 레지스트리**를 줄 수 있다.
프로덕션 경로는 모듈 전역 기본 세트(`_DEFAULT`)를 그대로 쓴다(하위호환).
"""

from __future__ import annotations

import os

import numpy as np
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# per-feature INPUT 히스토그램 버킷 (vitals/labs/demographics 스케일 공용, coarse)
_INPUT_BUCKETS = [0, 1, 5, 10, 20, 30, 40, 50, 60, 80, 100, 120, 150, 200, 250, 300]
_PROB_BUCKETS = [i / 20 for i in range(21)]  # 0.0..1.0 step .05


def _per_patient_enabled() -> bool:
    """SERVE_PER_PATIENT_GAUGE 를 호출 시점에 동적 판독(import 시 상수화 금지 — 토글 가능)."""
    return os.environ.get("SERVE_PER_PATIENT_GAUGE", "").strip().lower() in ("1", "true", "yes", "on")


def _aux_metrics_enabled() -> bool:
    """SEPSIS_SERVE_AUX_METRICS — 부가 계측(피처별 입력분포) on/off (2A NB3).

    **미설정 / 그 외 값 → ON**(기본 = 배포 계측 프로파일 arm-1). 프로덕션·데모는 계측을
    켠 채 도는 것이 정상이고 끄는 것은 벤치가 명시적으로 opt-in 할 때뿐 — 그래서 미설정은
    ON, 알 수 없는 값도 ON(관대한 파싱, 500 금지). **명시적 `0`/`false`/`off` 일 때만 OFF.**
    호출 시점 동적 판독(프로덕션 런타임 토글 가능); 벤치 앱은 기동 시점에 캡처해 넘긴다."""
    v = os.environ.get("SEPSIS_SERVE_AUX_METRICS")
    if v is None:
        return True
    return v.strip().lower() not in ("0", "false", "off")


class MetricSet:
    """서빙 인스턴스 하나의 Prometheus 지표 묶음.

    `registry` 를 주면 그 레지스트리에 등록 → **인스턴스 격리**(벤치가 여러 서버를 한
    프로세스에서 띄워도 부가계측 시계열이 서로 안 샌다). 안 주면 전역 기본 레지스트리.
    """

    def __init__(self, registry=None):
        r = registry if registry is not None else REGISTRY
        self.registry = r
        self.PREDICT_REQUESTS = Counter("serve_predict_requests_total", "POST /predict calls",
                                        registry=r)
        self.HEALTH_REQUESTS = Counter("serve_health_requests_total", "GET /health calls",
                                       registry=r)
        self.ALARMS = Counter("serve_alarms_total", "predictions with p >= tau", registry=r)
        self.LATENCY = Histogram("serve_predict_latency_seconds", "per-/predict latency",
                                 registry=r)
        self.PRED_PROB = Histogram("serve_pred_prob", "predicted risk probability",
                                   buckets=_PROB_BUCKETS, registry=r)
        # per-feature input distribution (covariate-drift foundation) — 부가 계측(게이트 대상).
        self.INPUT_FEATURE = Histogram("serve_input_feature_value", "observed input value per feature",
                                       ["feature"], buckets=_INPUT_BUCKETS, registry=r)
        self.INPUT_MISSING = Counter("serve_input_missing_total", "missing (NaN) inputs per feature",
                                     ["feature"], registry=r)
        # 환자별 최신 위험도 gauge — 무한 카디널리티 footgun이라 SERVE_PER_PATIENT_GAUGE 옵트인.
        self.PRED_PROB_LATEST = Gauge("serve_pred_prob_latest",
                                      "latest predicted risk per patient (OPT-IN — unbounded label)",
                                      ["patient_id"], registry=r)

    def record(self, latency_s: float, p: float, alarm: bool, raw_row: np.ndarray, feature_names,
               patient_id: str | None = None, *, aux: bool | None = None) -> None:
        """요청 1건 관측. `aux` 로 부가 계측(피처별 입력분포) 게이트를 준다:
        None → 호출 시점 env(`_aux_metrics_enabled`) 판독(프로덕션); bool → 그 값 사용
        (벤치 앱이 기동 시점 캡처값을 넘김). **latency·요청·확률 관측은 게이트 무관 항상 수행.**"""
        self.PREDICT_REQUESTS.inc()
        self.LATENCY.observe(latency_s)
        self.PRED_PROB.observe(p)
        if alarm:
            self.ALARMS.inc()
        if patient_id is not None and _per_patient_enabled():   # 옵트인 + pid 있을 때만
            self.PRED_PROB_LATEST.labels(patient_id=patient_id).set(p)
        aux_on = _aux_metrics_enabled() if aux is None else aux
        if aux_on:   # ★ 부가 계측 게이트(2A) — OFF면 피처별 시계열을 남기지 않는다.
            for name, v in zip(feature_names, raw_row):
                if np.isnan(v):
                    self.INPUT_MISSING.labels(feature=name).inc()
                else:
                    self.INPUT_FEATURE.labels(feature=name).observe(float(v))

    def render(self) -> tuple[bytes, str]:
        return generate_latest(self.registry), CONTENT_TYPE_LATEST


# 전역 기본 세트 — 프로덕션 GRU 서빙 경로가 그대로 쓴다(하위호환).
_DEFAULT = MetricSet(registry=REGISTRY)

# 하위호환 모듈 심볼(기존 코드가 metrics.LATENCY / metrics.HEALTH_REQUESTS 등으로 참조).
PREDICT_REQUESTS = _DEFAULT.PREDICT_REQUESTS
HEALTH_REQUESTS = _DEFAULT.HEALTH_REQUESTS
ALARMS = _DEFAULT.ALARMS
LATENCY = _DEFAULT.LATENCY
PRED_PROB = _DEFAULT.PRED_PROB
INPUT_FEATURE = _DEFAULT.INPUT_FEATURE
INPUT_MISSING = _DEFAULT.INPUT_MISSING
PRED_PROB_LATEST = _DEFAULT.PRED_PROB_LATEST


def record(latency_s: float, p: float, alarm: bool, raw_row: np.ndarray, feature_names,
           patient_id: str | None = None, *, aux: bool | None = None) -> None:
    _DEFAULT.record(latency_s, p, alarm, raw_row, feature_names, patient_id, aux=aux)


def render() -> tuple[bytes, str]:
    return _DEFAULT.render()
