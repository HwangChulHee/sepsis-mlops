"""회귀 — materialize 는 이미 최종화(.ready)된 버전을 덮어쓰지 않는다(D-retrain#3 무결성).

외부에서 같은 version 문자열을 재사용하면 감사·rollback 이 신뢰하는 archived 버전의
model/stats/reference 바이트가 제자리에서 바뀌어(run_id 는 옛 것 유지) rollback 이 검증된
것과 '다른' 가중치를 복원한다. 최종화된 버전은 FileExistsError 로 막고, 미완성(.ready 없음)
디렉토리로의 재시도는 허용한다.
"""
from __future__ import annotations

import pytest

from sepsis.retrain import deploy


def test_refuses_overwrite_of_finalized_version(tmp_path, rr, vr):
    out = deploy.materialize(rr, "v1", validation=vr, root=tmp_path)
    assert (out / ".ready").exists()          # 1차: 최종화됨
    with pytest.raises(FileExistsError):
        deploy.materialize(rr, "v1", validation=vr, root=tmp_path)  # 2차: 같은 version → 차단


def test_allows_retry_when_not_finalized(tmp_path, rr, vr):
    # .ready 가 없는(미완성) 디렉토리는 재시도 허용 — half-write 복구 경로.
    half = tmp_path / f"gru_{rr.featureset}@v2"
    half.mkdir(parents=True)
    (half / "model.pt").write_bytes(b"stale")  # .ready 는 없음
    out = deploy.materialize(rr, "v2", validation=vr, root=tmp_path)
    assert (out / ".ready").exists()
