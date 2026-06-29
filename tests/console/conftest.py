"""공유 픽스처/헬퍼 — console 백엔드 핸드오프 A TDD.

출처(이 셋만 신뢰): design/console/handoff_backend.md(명세부, 주 출처),
decisions.md(결정 4·5·5-A·5-B·6-A·7), handoff_backend_review.md(확정 계약).
**src/ 구현 코드는 일절 읽지 않았다** — 핸드오프가 명세한 심볼/시그니처/필드를
그대로 신뢰해 import·구성한다. 구현(src/sepsis/console/)이 없으니 지금은 RED 가 정상이다.

핸드오프가 못 박은 인터페이스(신뢰 근거):
- src/sepsis/console/audit.py
    - class AuditEvent(Base): id·ts·event_type·featureset·gate_passed·from_version·
      to_version·run_id·git_commit·gate_snapshot·actor_unverified(default "operator")·
      verified_subject(NULL)·reason(default "")  (handoff:55-69)
    - Base (declarative base) — AuditEvent 의 부모(handoff:55)
    - class AuditStore(url="sqlite:///console_audit.db"):
        append(**fields)->AuditEvent / last_active(fs)->AuditEvent|None /
        query(*, event_type=None, gate_passed=None, since=None, until=None, featureset=None)
      (handoff:103-108)
    - append-only 강제: before_flush(ORM UPDATE/DELETE 차단) + do_orm_execute(bulk 차단)
      를 모듈 import 시 전역 Session 에 등록(handoff:80-95)
- src/sepsis/console/service.py
    - approve(fs, version_id, *, actor, reason="")->dict (handoff:139)
    - rollback(fs, target_version_id, *, actor, reason="")->dict (handoff:159)
    - _version_dir(version_id)->Path = ARTIFACTS/version_id (handoff:34-35)
    - _require_consistent(fs, version_id)->None (handoff:37-40)
    - _reconcile_or_seed(fs) (handoff:197-216)
    - _propagate_and_confirm(fs, *, timeout_s=10, interval_s=0.5)->str (handoff:226-235)
    - 모듈 전역: ARTIFACTS, audit(=AuditStore 인스턴스), _LOCKS, deploy,
      _propagate_and_confirm, _trigger_reload, _get_health, _alert_missing_alias, lifespan
- src/sepsis/console/config.py (또는 __init__): CONSOLE_FEATURESETS (handoff:189)
- src/sepsis/console/api.py: FastAPI app (5 엔드포인트, handoff:115-129)

재활용(backend, console 밖 — 사용 허용): sepsis.retrain.deploy 의
active_version/swap/rollback 계약(handoff:11-13)을 *대역(fake)* 으로 교체해
console 오케스트레이션만 격리 검증한다(console-prep 의 patch_loaders 패턴과 동일 사상).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


# ===== version dir 빌더 (핸드오프 영속 파일 계약 — N1/5-B) =====
def make_version_dir(artifacts, fs, label, *, no_regression=True, run_id=None,
                     git_commit="deadbeef", ready=True, eps=0.02, with_data=False):
    """ARTIFACTS/gru_<fs>@<label>/ 에 meta.json·validation.json·retrain.json(+.ready) 기록.

    - git_commit 은 **retrain.json 에만** 둔다(meta.json 엔 없음 — deploy.py:49-52/64, MJ1).
    - run_id 는 meta.json·retrain.json 양쪽에 둔다(연결 키, deploy.py:52).
    - 반환 = (version_id 디렉토리명, dir Path).
    """
    version_id = f"gru_{fs}@{label}"
    d = Path(artifacts) / version_id
    d.mkdir(parents=True, exist_ok=True)
    if run_id is None:
        run_id = f"runid-{label}"
    # meta.json — git_commit 없음(deploy.py:49-52)
    (d / "meta.json").write_text(json.dumps({
        "featureset": fs, "hp": {"hidden": 8}, "input_dim": 4,
        "tau": 0.5, "version": label, "trained_on": "2026-01-01",
        "run_id": run_id,
    }))
    # validation.json — ValidationResult 전 필드 + 영속 주입(eps·validated_at)
    (d / "validation.json").write_text(json.dumps({
        "no_regression": no_regression,
        "bholdout_util": 0.42, "bholdout_prauc": 0.31,
        "new_aval_util": 0.40, "old_aval_util": 0.38,
        "new_aval_prauc": 0.30, "old_aval_prauc": 0.28,
        "eps": eps, "cross_site_claim": "A->B holds",
        "distribution": {"ks": 0.01}, "note": "ok",
        "validated_at": "2026-01-01T00:00:00Z",
    }))
    # retrain.json — git_commit 의 단일 출처(deploy.py:64)
    (d / "retrain.json").write_text(json.dumps({
        "epochs": 2, "val_loss": 0.12, "b_split_seed": 42,
        "n_train_pids": 4, "n_b_retrain": 3, "n_b_holdout": 2,
        "run_id": run_id, "git_commit": git_commit,
    }))
    if with_data:
        (d / "model.pt").write_bytes(b"MODEL-BYTES")
        (d / "pre.npz").write_bytes(b"PRE-NPZ-BYTES")
        (d / "reference.npz").write_bytes(b"REF-NPZ-BYTES")
    if ready:
        (d / ".ready").write_text(json.dumps({"complete": True}))
    return version_id, d


# ===== deploy 백엔드 대역 — 문서화된 계약(handoff:11-13)만 재현 =====
class FakeDeploy:
    """deploy.active_version/swap/rollback 의 *문서화된 계약*을 재현하는 대역.

    - active_version(fs)->str|None : alias 가 가리키는 **디렉토리명** 반환(handoff:11).
    - swap(fs, version_dir, *, validation, approved, root=...)->prev :
        approved is not True → PermissionError, getattr(validation,"no_regression") falsy →
        ValueError, 통과 시 alias 전환 후 **이전 활성 디렉토리명** 반환(handoff:12).
    - rollback(fs, prev_version_name, *, root=...)->None : alias 만 되돌림(handoff:13).
    swap_delay 로 임계구간 내 지연을 주입해 직렬화 경합을 재현한다.
    """

    def __init__(self):
        self.active: dict[str, str] = {}
        self.swap_calls = []      # (fs, to_dirname, prev, validation_obj)
        self.rollback_calls = []  # (fs, target)
        self.swap_delay = 0.0

    def set_active(self, fs, version_id):
        self.active[fs] = version_id

    def active_version(self, fs, *, root=None):
        return self.active.get(fs)

    def swap(self, fs, version_dir, *, validation, approved, root=None):
        if approved is not True:
            raise PermissionError("approved is not True")
        if not getattr(validation, "no_regression", False):
            raise ValueError("no_regression falsy (REGRESSED)")
        if self.swap_delay:
            import time
            time.sleep(self.swap_delay)
        prev = self.active.get(fs)
        to_name = os.path.basename(os.fspath(version_dir))
        self.active[fs] = to_name
        self.swap_calls.append((fs, to_name, prev, validation))
        return prev

    def rollback(self, fs, prev_version_name, *, root=None):
        self.rollback_calls.append((fs, prev_version_name))
        self.active[fs] = prev_version_name
        return None


@pytest.fixture
def console(tmp_path, monkeypatch):
    """console.service 를 격리 구동하는 환경.

    구현이 없으면 이 fixture import 에서 RED(ModuleNotFoundError) — 정상.
    """
    # lazy import: 미구현 시 세션 전체가 아니라 *이 fixture 를 쓰는 테스트*만 RED
    from sepsis.retrain import deploy
    import sepsis.console.service as service
    from sepsis.console.audit import AuditStore

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    store = AuditStore(url=f"sqlite:///{tmp_path / 'audit.db'}")
    fd = FakeDeploy()

    # deploy 백엔드를 대역으로(default-arg ARTIFACTS 동결 회피 + 진짜 alias 불필요)
    monkeypatch.setattr(deploy, "active_version", fd.active_version)
    monkeypatch.setattr(deploy, "swap", fd.swap)
    monkeypatch.setattr(deploy, "rollback", fd.rollback)
    # service 모듈 전역 주입 — [검증 필요] 선행: service.ARTIFACTS / service.audit 라는
    # 모듈 전역명이 실제로 존재(핸드오프가 `ARTIFACTS`·`audit.append` 로 명명)
    monkeypatch.setattr(service, "ARTIFACTS", artifacts, raising=False)
    monkeypatch.setattr(service, "audit", store, raising=False)
    # 전파 폴링은 propagation 테스트만 실측 — 그 외엔 confirmed 스텁으로 빠르게.
    # 실측용 원본은 real_propagate 로 보존해 propagation 테스트가 다시 부른다.
    real_propagate = getattr(service, "_propagate_and_confirm", None)
    monkeypatch.setattr(service, "_propagate_and_confirm",
                        lambda fs, **k: "confirmed", raising=False)

    def mk(label, **kw):
        return make_version_dir(artifacts, kw.pop("fs", "vitals"), label, **kw)

    return SimpleNamespace(service=service, deploy=deploy, store=store, fd=fd,
                           artifacts=artifacts, monkeypatch=monkeypatch, mk=mk,
                           real_propagate=real_propagate)
