"""성공기준 5 — rollback 경로: 재검증 면제·승인+감사 필수·prev 사전 캡처.

검증 대상(handoff:159-169, 175, 252, 결정 5-A·7-3):
- validation 재검증 없이 실행되나 감사 1건 필수(event_type=ROLLBACK, gate_passed=NULL).
- 롤백 prev = 사전 active_version 캡처(mn-c) — deploy.rollback 이 prev 미반환이라 콘솔이 사전 읽기.
- 롤백 타겟·from/to 가 모두 디렉토리명(B1).

src/ 구현 코드는 읽지 않았다.
"""
from __future__ import annotations


# ===== 롤백 = 감사 1건, gate_passed NULL, from/to 디렉토리명 =====
def test_rollback_records_audit_with_null_gate(console):
    # 성공기준 5
    fs = "vitals"
    console.fd.set_active(fs, "gru_vitals@v3")        # 현재 활성 = v3
    target, _ = console.mk("v2")                       # 과거 검증된 v2 로 복귀
    out = console.service.rollback(fs, target, actor="oncall", reason="incident")

    ev = console.store.last_active(fs)
    assert ev.event_type == "ROLLBACK"
    assert ev.gate_passed is None, "ROLLBACK 의 gate_passed 는 NULL 이어야 함(검증 면제)"
    assert ev.from_version == "gru_vitals@v3"          # 사전 캡처 prev(mn-c)
    assert ev.to_version == "gru_vitals@v2"            # 디렉토리명(B1)
    assert ev.actor_unverified == "oncall"
    assert out["prev"] == "gru_vitals@v3"
    assert out["active"] == "gru_vitals@v2"


# ===== prev 는 롤백 *전* active_version 캡처 (mn-c) =====
def test_rollback_prev_captured_before_alias_change(console):
    # 성공기준 5 (mn-c) — deploy.rollback 은 prev 미반환 → 콘솔이 사전 active 읽기
    fs = "vitals"
    console.fd.set_active(fs, "gru_vitals@v5")
    target, _ = console.mk("v4")
    console.service.rollback(fs, target, actor="op")
    ev = console.store.last_active(fs)
    # prev 가 롤백 후 active(v4)가 아니라 롤백 전 active(v5)여야 함
    assert ev.from_version == "gru_vitals@v5"
    assert console.fd.rollback_calls == [(fs, "gru_vitals@v4")]


# ===== validation 재검증 면제: validation.json 없는 타겟도 롤백 가능 =====
def test_rollback_skips_validation_recheck(console):
    # 성공기준 5 (결정 5-A) — 과거 검증 버전 복귀는 no_regression 재검증 요구 안 함
    fs = "vitals"
    console.fd.set_active(fs, "gru_vitals@v3")
    # validation.json/.ready 없는 디렉토리(과거 버전 잔존)로도 롤백 성공해야 함
    target, tdir = console.mk("v1", ready=False)
    (tdir / "validation.json").unlink()  # 재검증 게이트가 있으면 여기서 실패할 것
    out = console.service.rollback(fs, target, actor="op")
    assert out["active"] == "gru_vitals@v1"
    assert console.store.last_active(fs).event_type == "ROLLBACK"
