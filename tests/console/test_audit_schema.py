"""성공기준 2 — 감사 스키마: 게이트 스냅샷·식별자·출처 파일·기본값.

검증 대상(handoff:55-74, 249, 결정 4 / N1·MJ1·M4):
- APPROVE 레코드의 gate_snapshot = 승인 대상 version dir 의 validation.json **사본**.
- gate_passed == validation.json 의 no_regression.
- from_version·to_version 이 **디렉토리명**(gru_<fs>@<v>).
- git_commit 이 **retrain.json** 에서 채워짐(meta.json 아님, NULL 아님 — MJ1).
- run_id 가 meta.json.run_id 에서 채워짐.
- actor_unverified 기본값 "operator", verified_subject 는 NULL(M4).

src/ 구현 코드는 읽지 않았다.
"""
from __future__ import annotations

import json


# ===== 스키마 기본값: actor_unverified 기본 "operator", verified_subject NULL =====
def test_append_defaults_actor_and_verified_subject(tmp_path):
    # 성공기준 2 (M4)
    from sepsis.console.audit import AuditStore
    store = AuditStore(url=f"sqlite:///{tmp_path / 'a.db'}")
    ev = store.append(event_type="APPROVE", featureset="vitals",
                      to_version="gru_vitals@v1")
    assert ev.actor_unverified == "operator", "actor_unverified 기본값이 'operator' 아님(M4)"
    assert ev.verified_subject is None, "verified_subject 는 MVP 에서 NULL 이어야 함(M4 예약 컬럼)"
    assert ev.reason == ""  # reason 기본값(handoff:69)


# ===== APPROVE 레코드 전 필드: 스냅샷 사본·식별자·출처 파일 (end-to-end approve) =====
def test_approve_audit_record_fields(console):
    # 성공기준 2 — approve 가 남기는 APPROVE 레코드의 모든 계약 필드
    svc = console.service
    fs = "vitals"
    # prev(현재 활성) = v1, 승인 대상 = v2 (no_regression=True, .ready, git_commit 은 retrain.json)
    console.mk("v1")  # prev 디렉토리(파일은 안 읽힘)
    console.fd.set_active(fs, "gru_vitals@v1")
    v2, v2dir = console.mk("v2", run_id="run-v2", git_commit="cafe1234")

    svc.approve(fs, v2, actor="dr.kim", reason="looks good")

    ev = console.store.last_active(fs)
    assert ev.event_type == "APPROVE"
    # 식별자 = 디렉토리명(B1)
    assert ev.from_version == "gru_vitals@v1"
    assert ev.to_version == "gru_vitals@v2"
    # gate_passed == validation.json.no_regression
    assert ev.gate_passed is True
    # gate_snapshot = validation.json 통째 사본(박제)
    disk_val = json.loads((v2dir / "validation.json").read_text())
    assert ev.gate_snapshot == disk_val, "gate_snapshot 이 version dir validation.json 사본이 아님(N1)"
    # git_commit 출처 = retrain.json (meta.json 아님, NULL 아님 — MJ1)
    assert ev.git_commit == "cafe1234"
    # run_id 출처 = meta.json.run_id
    assert ev.run_id == "run-v2"
    # actor 는 호출자가 준 미검증 입력
    assert ev.actor_unverified == "dr.kim"
    assert ev.reason == "looks good"


# ===== git_commit 이 meta.json 이 아니라 retrain.json 에서 온다는 점을 분리 증명 (MJ1) =====
def test_git_commit_source_is_retrain_json_not_meta(console):
    # 성공기준 2 (MJ1) — meta.json 엔 git_commit 이 아예 없으므로,
    # 감사 git_commit 이 비지 않고 retrain.json 값이면 출처가 retrain.json 임이 증명된다.
    svc = console.service
    fs = "vitals"
    console.fd.set_active(fs, "gru_vitals@v1")
    v2, v2dir = console.mk("v2", git_commit="abc99999")
    # meta.json 에 git_commit 이 없음을 명시 확인(make_version_dir 계약)
    assert "git_commit" not in json.loads((v2dir / "meta.json").read_text())

    svc.approve(fs, v2, actor="op")
    ev = console.store.last_active(fs)
    assert ev.git_commit == "abc99999", "git_commit 이 retrain.json 에서 안 옴(meta.json 참조 시 NULL — MJ1 회귀)"
