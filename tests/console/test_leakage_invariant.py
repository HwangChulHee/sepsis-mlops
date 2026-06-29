"""성공기준 9 — 누수 불변: 콘솔은 노출·기록 계층 한정(데이터 불변).

검증 대상(handoff:256, CLAUDE.md 누수 방지 대원칙):
- 콘솔 approve/rollback 은 version dir 의 데이터 산출물(model.pt·pre.npz·reference.npz·
  meta.json·validation.json·retrain.json)을 **건드리지 않는다**(읽기 전용).
- 환자 단위 B 분할·train-only stats·0-fill 금지·mask OFF 는 재학습 계층 소관이지
  콘솔이 재계산/변형하지 않는다 — 콘솔이 데이터 파일을 못 쓰면 누수 통로가 안 생긴다.

이 성공기준은 "콘솔이 전처리/정규화를 안 한다"는 *부재(不在)* 계약이라, 데이터 파일
불변(behavioral)으로 고정한다. src/ 구현 코드는 읽지 않았다.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


def _snapshot(d: Path):
    """버전 dir 안 모든 파일의 {상대경로: sha256} 스냅샷."""
    out = {}
    for p in sorted(d.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(d))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


# ===== approve 가 version dir 데이터 산출물을 변형하지 않음 =====
def test_approve_does_not_mutate_version_dir(console):
    # 성공기준 9 — 콘솔은 읽기·기록(감사) 계층, 데이터 파일 불변
    fs = "vitals"
    console.fd.set_active(fs, "gru_vitals@v1")
    v2, v2dir = console.mk("v2", with_data=True)

    before = _snapshot(v2dir)
    console.service.approve(fs, v2, actor="op")
    after = _snapshot(v2dir)

    assert before == after, f"approve 가 version dir 파일을 변형/추가함: {set(after) ^ set(before)}"
    # 정규화/0-fill 산출물 같은 신규 파일이 생기지 않았다
    assert set(after) == set(before)


# ===== rollback 도 타겟 dir 데이터를 변형하지 않음 =====
def test_rollback_does_not_mutate_version_dir(console):
    # 성공기준 9
    fs = "vitals"
    console.fd.set_active(fs, "gru_vitals@v3")
    target, tdir = console.mk("v2", with_data=True)

    before = _snapshot(tdir)
    console.service.rollback(fs, target, actor="op")
    after = _snapshot(tdir)

    assert before == after, "rollback 이 version dir 데이터를 변형함(누수 계층 침범)"
