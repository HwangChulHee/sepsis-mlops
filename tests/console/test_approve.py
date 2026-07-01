"""성공기준 4 — approve 경로: .ready 게이트·복원·REGRESSED 거부·정상 반환.

검증 대상(handoff:139-157, 172-174, 251, 결정 5·5-A·5-B):
- .ready 없는 version 승인 → 거부(미완성 후보), swap·audit 미발생.
- validation.json → SimpleNamespace 복원으로 deploy.swap 이 getattr(no_regression) 정합(5-B).
- REGRESSED(no_regression=False) → ValueError(API 가 422 변환), audit 미발생.
- 정상 승인 → prev·active 반환 + 감사 1건, 순서 swap(②)→audit(③).

src/ 구현 코드는 읽지 않았다.
"""
from __future__ import annotations

import pytest


# ===== .ready 없는 미완성 후보 승인 거부 (422) =====
def test_approve_rejects_incomplete_without_ready(console):
    # 성공기준 4 — _require_ready: .ready 없으면 거부(handoff:142)
    # [검증 필요] 선행: _require_ready 가 던지는 예외 타입(API 가 422 로 변환) 미명세 →
    # 여기선 "예외 발생 + swap/audit 미발생" 계약만 고정.
    console.fd.set_active("vitals", "gru_vitals@v1")
    v2, _ = console.mk("v2", ready=False)  # .ready 없음 = 미완성
    with pytest.raises((ValueError, FileNotFoundError, PermissionError)):
        console.service.approve("vitals", v2, actor="op")
    assert console.fd.swap_calls == [], "미완성 후보가 swap 까지 도달했다(.ready 게이트 누락)"
    assert console.store.query(featureset="vitals") == [], "거부됐는데 감사가 기록됨"


# ===== REGRESSED 거부 = ValueError (deploy.swap), audit 미발생 =====
def test_approve_regressed_raises_valueerror(console):
    # 성공기준 4 (M3) — no_regression=False → deploy.swap ValueError, API 가 422 로 변환
    console.fd.set_active("vitals", "gru_vitals@v1")
    v2, _ = console.mk("v2", no_regression=False)
    with pytest.raises(ValueError):
        console.service.approve("vitals", v2, actor="op")
    # swap 이 호출됐으나 ValueError 로 거부 → audit append 는 swap 이후라 안 남는다
    assert console.store.query(featureset="vitals") == [], "REGRESSED 인데 감사가 남음(순서 위반)"


# ===== 복원 객체가 dict 아닌 속성-접근 가능 객체 (5-B) =====
def test_approve_restores_validation_as_object_not_dict(console):
    # 성공기준 4 (5-B) — deploy.swap 에 건네진 validation 이 getattr(no_regression) 정합
    console.fd.set_active("vitals", "gru_vitals@v1")
    v2, _ = console.mk("v2", no_regression=True)
    console.service.approve("vitals", v2, actor="op")
    assert console.fd.swap_calls, "swap 미호출"
    _, _, _, validation = console.fd.swap_calls[-1]
    assert not isinstance(validation, dict), "validation 이 dict 로 전달됨 — getattr 접근 실패(5-B 위반)"
    assert getattr(validation, "no_regression", None) is True


# ===== 정상 승인: prev·active 반환 + 감사 1건 =====
def test_approve_success_returns_prev_active_and_audits_once(console):
    # 성공기준 4 — 반환 dict 와 감사 1건
    fs = "vitals"
    console.fd.set_active(fs, "gru_vitals@v1")
    v2, _ = console.mk("v2")
    out = console.service.approve(fs, v2, actor="op")
    assert out["prev"] == "gru_vitals@v1"
    assert out["active"] == "gru_vitals@v2"
    assert "event_id" in out and out["event_id"] is not None
    rows = console.store.query(featureset=fs)
    assert len(rows) == 1 and rows[0].event_type == "APPROVE"
    # alias 도 새 버전으로 이동(fake deploy 상태)
    assert console.fd.active_version(fs) == "gru_vitals@v2"


# ===== 순서: swap(②) → audit(③). swap 이 audit 보다 먼저 발생 =====
def test_approve_swap_before_audit(console):
    # 성공기준 4 (결정 7-2) — ②swap → ③audit. swap 실패면 audit 안 남음(위 REGRESSED 로 보강).
    fs = "vitals"
    console.fd.set_active(fs, "gru_vitals@v1")
    v2, _ = console.mk("v2")

    events = []
    real_swap = console.fd.swap
    def spy_swap(*a, **k):
        events.append("swap")
        return real_swap(*a, **k)
    console.monkeypatch.setattr(console.deploy, "swap", spy_swap)

    real_append = console.store.append
    def spy_append(*a, **k):
        events.append("audit")
        return real_append(*a, **k)
    console.monkeypatch.setattr(console.store, "append", spy_append)

    console.service.approve(fs, v2, actor="op")
    assert events == ["swap", "audit"], f"swap→audit 순서 위반: {events}"
