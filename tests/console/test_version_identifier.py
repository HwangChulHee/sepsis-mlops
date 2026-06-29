"""집중검증 2 / B1 — 버전 식별자 규약 = 디렉토리명 단일화.

검증 대상(handoff:24-44, 결정 5·7):
- _version_dir(version_id) = ARTIFACTS / version_id (접두사 재부착 금지 — B2 이중접두사 차단).
- _require_consistent(fs, version_id): 디렉토리명이 featureset 소속이면 통과,
  교차-fs 또는 맨버전 단독이면 ValueError(교차-fs 오승인 차단).
- 맨버전(v3) 단독은 어떤 경로에도 등장하지 않음 — _require_consistent 가 거부.

src/ 구현 코드는 읽지 않았다.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ===== _version_dir 은 접두사 재부착 없이 ARTIFACTS/version_id =====
def test_version_dir_no_prefix_reattach(console):
    # 집중검증 2 (B2) — ARTIFACTS / "gru_vitals@v3", 이중접두사 없음
    svc = console.service
    p = svc._version_dir("gru_vitals@v3")
    assert Path(p) == console.artifacts / "gru_vitals@v3"
    # 옛 v1 버그(ARTIFACTS / f"gru_{fs}@{...}")라면 gru_vitals@gru_vitals@v3 이 됨 — 금지
    assert "gru_vitals@gru_vitals@" not in str(p)


# ===== _require_consistent: 소속 OK / 교차-fs raise / 맨버전 raise =====
def test_require_consistent_accepts_in_featureset(console):
    # 집중검증 2 — 같은 featureset 소속 디렉토리명은 통과
    console.service._require_consistent("vitals", "gru_vitals@v3")  # 예외 없음


def test_require_consistent_rejects_cross_featureset(console):
    # 집중검증 2 — 교차-fs 오승인 차단(handoff:39-40)
    with pytest.raises(ValueError):
        console.service._require_consistent("vitals", "gru_labs@v3")


def test_require_consistent_rejects_bare_version(console):
    # 집중검증 2 (B1) — 맨버전(v3) 단독은 startswith("gru_vitals@") 불만족 → 거부
    with pytest.raises(ValueError):
        console.service._require_consistent("vitals", "v3")


# ===== approve 가 교차-fs version_id 를 거부(가드 전파) =====
def test_approve_rejects_cross_featureset_version(console):
    # 집중검증 2 — approve 진입에서 _require_consistent 로 교차-fs 차단(handoff:140)
    console.mk("v9", fs="labs")  # gru_labs@v9 디렉토리 자체는 존재
    with pytest.raises(ValueError):
        console.service.approve("vitals", "gru_labs@v9", actor="op")
    # swap 까지 가지 않았다(가드가 앞단에서 끊음)
    assert console.fd.swap_calls == []
