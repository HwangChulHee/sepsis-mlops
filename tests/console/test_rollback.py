"""성공기준 5 — rollback 경로: 재검증 면제·승인+감사 필수·prev 사전 캡처.

검증 대상(handoff:159-169, 175, 252, 결정 5-A·7-3):
- validation 재검증 없이 실행되나 감사 1건 필수(event_type=ROLLBACK, gate_passed=NULL).
- 롤백 prev = 임계구간 내 사전 active_version 캡처(mn-c, 경합 오염 차단). deploy.rollback 도 H4r
  대칭화로 prev 를 반환하나, 콘솔은 설계상 임계구간 안에서 읽은 값을 감사에 쓴다.
- 롤백 타겟·from/to 가 모두 디렉토리명(B1).

src/ 구현 코드는 읽지 않았다.
"""
from __future__ import annotations

import pytest


# ===== 롤백 = 감사 1건, gate_passed NULL, from/to 디렉토리명 =====
def test_rollback_records_audit_with_null_gate(console):
    # 성공기준 5
    fs = "vitals"
    console.fd.set_active(fs, "gru_vitals@v3")        # 현재 활성 = v3
    target, _ = console.mk("v2")                       # 과거 검증된 v2 로 복귀
    # H4r: 롤백 대상은 과거 champion(archived)이어야 함 → 감사 이력 시드(셋업 보정, BR2-1)
    console.store.append(event_type="APPROVE", featureset=fs, to_version=target, gate_passed=True)
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
    # 성공기준 5 (mn-c) — 콘솔이 임계구간 안에서 사전 active 읽기로 prev 캡처(deploy.rollback 반환과 무관, 경합 차단)
    fs = "vitals"
    console.fd.set_active(fs, "gru_vitals@v5")
    target, _ = console.mk("v4")
    # H4r: 과거 champion 시드(셋업 보정, BR2-1)
    console.store.append(event_type="APPROVE", featureset=fs, to_version=target, gate_passed=True)
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
    # H4r: validation.json 없이도 과거활성 이력(archived)만으로 롤백 통과함을 보임 —
    #   게이트를 재검증이 아니라 감사 이력으로 건다(5-A 재검증 면제 유지, BR2-1)
    console.store.append(event_type="APPROVE", featureset=fs, to_version=target, gate_passed=True)
    out = console.service.rollback(fs, target, actor="op")
    assert out["active"] == "gru_vitals@v1"
    assert console.store.last_active(fs).event_type == "ROLLBACK"


# ===== H4r: archived 아닌 타겟(감사 이력 없는 REGRESSED) 롤백 거부 (BR2-1) =====
def test_rollback_rejects_non_archived_target(console):
    # BR2-1 — 백엔드 무게이트 우회 차단: 과거 활성 이력 없는 REGRESSED 버전 롤백 금지
    fs = "vitals"
    console.fd.set_active(fs, "gru_vitals@champ")
    # REGRESSED challenger, 감사 이력 없음(과거 활성 아님) → archived 아님
    target, _ = console.mk("bad", no_regression=False)
    with pytest.raises(ValueError):
        console.service.rollback(fs, target, actor="attacker", reason="bypass")
    # alias 불변, deploy.rollback 미호출, ROLLBACK 감사 없음
    assert console.fd.active_version(fs) == "gru_vitals@champ"
    assert console.fd.rollback_calls == []
    assert [e for e in console.store.query(featureset=fs) if e.event_type == "ROLLBACK"] == []


# ===== H4r: 진짜 과거 champion 롤백은 성공 (회귀 방지, BR2-1) =====
def test_rollback_allows_genuine_past_champion(console):
    # BR2-1 — 안전 게이트가 정당한 롤백(과거 검증 champion=archived)은 막지 않음
    fs = "vitals"
    console.fd.set_active(fs, "gru_vitals@v9")
    target, _ = console.mk("v8")
    # 과거 한때 활성이었던 champion 으로 시드 → archived
    console.store.append(event_type="APPROVE", featureset=fs, to_version=target, gate_passed=True)
    out = console.service.rollback(fs, target, actor="op", reason="genuine")
    assert out["active"] == "gru_vitals@v8"
    assert console.store.last_active(fs).event_type == "ROLLBACK"
    assert console.fd.rollback_calls == [(fs, "gru_vitals@v8")]
