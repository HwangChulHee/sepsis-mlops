"""리플레이어 라운드 (다) — 환자별 최신 위험도 Gauge(옵트인) TDD.

권위 출처(이것만 신뢰): design/replay/handoff_round_c.md §2(카디널리티 결정)·§3.1·§5-1·§5-2.
환자별 라벨 Gauge는 무한 카디널리티 footgun이라 **기본 OFF**, `SERVE_PER_PATIENT_GAUGE=1`
일 때만 기록한다(프로젝트 '결측 마스크 기본 OFF' 철학과 동형). 플래그는 호출 시점 동적 판독.

커버하는 합격기준(§5): 1 Gauge 옵트인(OFF=시계열 0, ON=최신 p) · 2 record B-호환.
"""
from __future__ import annotations

import numpy as np
from prometheus_client import REGISTRY

from sepsis.serve import metrics


def _latest(pid: str):
    """REGISTRY에서 serve_pred_prob_latest{patient_id=pid} 표본값(없으면 None)."""
    return REGISTRY.get_sample_value("serve_pred_prob_latest", {"patient_id": pid})


def _row():
    return np.array([1.0, 2.0], dtype=np.float32)


def test_gauge_off_by_default_no_series(monkeypatch):
    """§2/§5-1: 플래그 없으면(기본 OFF) 환자별 시계열이 안 생긴다(카디널리티 footgun 차단)."""
    monkeypatch.delenv("SERVE_PER_PATIENT_GAUGE", raising=False)
    metrics.record(0.001, 0.42, False, _row(), ["HR", "O2Sat"], patient_id="off-default")
    assert _latest("off-default") is None


def test_gauge_off_when_flag_falsey(monkeypatch):
    """§2: 0/false 같은 falsey 값은 OFF 로 친다."""
    monkeypatch.setenv("SERVE_PER_PATIENT_GAUGE", "0")
    metrics.record(0.001, 0.55, True, _row(), ["HR", "O2Sat"], patient_id="off-zero")
    assert _latest("off-zero") is None


def test_gauge_on_records_latest_p(monkeypatch):
    """§5-1: ON이면 patient_id에 p가 박히고, 재기록 시 *최신* 값으로 갱신(Gauge=최신)."""
    monkeypatch.setenv("SERVE_PER_PATIENT_GAUGE", "1")
    metrics.record(0.001, 0.20, False, _row(), ["HR", "O2Sat"], patient_id="on-pat")
    assert _latest("on-pat") == 0.20
    metrics.record(0.001, 0.70, True, _row(), ["HR", "O2Sat"], patient_id="on-pat")
    assert _latest("on-pat") == 0.70   # 분포(Histogram)가 아니라 최신값


def test_record_backward_compatible_without_patient_id(monkeypatch):
    """§5-2 / F-c4: patient_id 없이 5-인자 호출이 그대로 동작(기존 호출부 비파괴)."""
    monkeypatch.setenv("SERVE_PER_PATIENT_GAUGE", "1")
    # 예외 없이 통과해야 하고, 라벨 시계열을 만들지 않는다(patient_id 미지정).
    metrics.record(0.001, 0.33, False, _row(), ["HR", "O2Sat"])
    # 등록 자체는 됐는지(메트릭 존재) — 표본 조회가 에러 없이 None/숫자를 돌려주면 등록됨.
    assert _latest("nonexistent-pid") is None
