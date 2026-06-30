"""H4r 방어 심화 (BR2-1) — deploy.rollback 이 swap 과 대칭인지 deploy 레벨에서 검증.

대상: src/sepsis/retrain/deploy.py::rollback
- approved is not True → PermissionError (alias 불변): 콘솔 API 우회 직접 호출에 대한 백스톱.
- approved=True → alias 를 previous_version_name 으로 되돌리고 **이전 활성 디렉토리명(prev)** 반환.

콘솔(service.rollback)이 archived 게이트를 강제하지만, deploy.rollback 을 직접 import 하는
경로는 그 게이트를 우회한다 — 그래서 swap 처럼 approved 가드를 둔다(decisions.md 5-A·H4r).
실제 deploy 함수를 tmp artifacts root + 진짜 심링크로 격리 구동한다(FakeDeploy 아님).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sepsis.retrain import deploy


def _seed(root: Path, fs: str, label: str) -> str:
    """gru_<fs>@<label> 버전 디렉토리 생성, 디렉토리명 반환."""
    version_id = f"gru_{fs}@{label}"
    (Path(root) / version_id).mkdir(parents=True, exist_ok=True)
    return version_id


def test_rollback_requires_approval(tmp_path):
    # approved 가드 — 미승인이면 PermissionError, alias 불변
    fs = "vitals"
    new = _seed(tmp_path, fs, "new")
    old = _seed(tmp_path, fs, "old")
    deploy.set_active(fs, tmp_path / new, root=tmp_path)   # 현재 활성 = new
    assert deploy.active_version(fs, root=tmp_path) == new

    with pytest.raises(PermissionError):
        deploy.rollback(fs, old, approved=False, root=tmp_path)
    # 차단됐으므로 alias 는 그대로 new
    assert deploy.active_version(fs, root=tmp_path) == new


def test_rollback_approved_returns_prev_and_swaps(tmp_path):
    # swap 과 대칭 — approved=True 면 alias 를 old 로 되돌리고 이전 활성(new) 반환
    fs = "vitals"
    new = _seed(tmp_path, fs, "new")
    old = _seed(tmp_path, fs, "old")
    deploy.set_active(fs, tmp_path / new, root=tmp_path)

    prev = deploy.rollback(fs, old, approved=True, root=tmp_path)

    assert prev == new                                     # 이전 활성 반환(롤백 prev)
    assert deploy.active_version(fs, root=tmp_path) == old  # alias 되돌려짐
