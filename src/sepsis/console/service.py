"""구현 2·3 — /console 서비스 계층: 직렬화 경계 + 감사 강제 + 화해/전파 + versions.

결정 5·5-A·5-B·6-A·7. deploy/validate/bundle를 호출만(로직 재구현 0). 버전 식별자 규약 =
**버전 디렉토리명**(B1): 감사 from/to·롤백 타겟·전파 타겟·화해 비교가 전부 `gru_<fs>@<v>`.
맨버전은 list_versions *응답 표면*에서만(행 표시용). 경로화는 `_version_dir`만(접두 재부착 금지, B2).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from sepsis.console.audit import AuditStore
from sepsis.console.config import CONSOLE_FEATURESETS
from sepsis.retrain import deploy

# === 모듈 전역(테스트 fixture가 ARTIFACTS·audit을 임시값으로 override) ===
ARTIFACTS: Path = deploy.ARTIFACTS                  # = C.ROOT/deploy/artifacts (deploy.py:27와 단일 출처)
# 감사 DB는 env(CONSOLE_AUDIT_DB_URL)로 PVC 경로 주입. env 없으면 기존 상대경로(하위호환) =
# 비영속이므로 프로덕션 매니페스트가 sqlite:////app/auditdb/console_audit.db 를 반드시 주입.
# 프로덕션은 env가, 테스트는 fixture(conftest monkeypatch)가 이 전역을 교체한다.
audit: AuditStore = AuditStore(os.environ.get("CONSOLE_AUDIT_DB_URL", "sqlite:///console_audit.db"))
MLFLOW_UI_BASE = os.environ.get("MLFLOW_UI_BASE")   # None → mlflow_link 폴백 null(6-A)

_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)   # featureset 단위 락(1프로세스 전제)
_log = logging.getLogger("console")


# === 버전 식별자 규약 (B1·B2) ===
def _version_dir(version_id: str) -> Path:
    return ARTIFACTS / version_id                   # 접두사 재부착 금지 — B2 이중접두사 차단


def _require_consistent(fs: str, version_id: str) -> None:
    if not version_id.startswith(f"gru_{fs}@"):     # 교차-fs/맨버전 단독 차단(→ API 422)
        raise ValueError(f"version {version_id!r} not in featureset {fs!r}")


def _strip_prefix(fs: str, version_id: str | None) -> str | None:
    if version_id is None:
        return None
    prefix = f"gru_{fs}@"
    return version_id[len(prefix):] if version_id.startswith(prefix) else version_id


# === version dir JSON 리더(콘솔 신규 헬퍼 — 모델 가중치 미로드, 메타 JSON만) ===
def _read_json(version_dir: Path, name: str) -> dict:
    p = version_dir / name
    return json.loads(p.read_text()) if p.exists() else {}


def _read_meta(version_dir: Path) -> dict:
    return _read_json(version_dir, "meta.json")


def _read_validation(version_dir: Path) -> dict:
    return _read_json(version_dir, "validation.json")


def _read_retrain(version_dir: Path) -> dict:
    return _read_json(version_dir, "retrain.json")


def _read_gate_snapshot(version_dir: Path) -> dict:
    return _read_validation(version_dir)            # validation.json 통째 사본(박제, N1)


def _restore_validation(version_dir: Path) -> SimpleNamespace:
    # validation.json → SimpleNamespace: deploy.swap이 getattr(no_regression)로 속성 접근(5-B)
    return SimpleNamespace(**_read_validation(version_dir))


def _require_ready(version_dir: Path) -> None:
    if not (version_dir / ".ready").exists():       # .ready 없으면 미완성 후보 — 승인 거부(API 422)
        raise FileNotFoundError(f"{version_dir.name} is not ready (no .ready marker)")


# === 쓰기 2개 — 직렬화 경계 + 감사 강제 (결정 5-A·7) ===
def approve(fs: str, version_id: str, *, actor: str, reason: str = "") -> dict:
    _require_consistent(fs, version_id)             # version_id = 디렉토리명, 교차-fs 가드(422)
    version_dir = _version_dir(version_id)          # ARTIFACTS / version_id — 접두사 재부착 없음(B2)
    _require_ready(version_dir)                      # .ready 게이트
    val = _restore_validation(version_dir)          # 5-B
    snap = _read_gate_snapshot(version_dir)         # 박제용 사본(N1)
    meta = _read_meta(version_dir)                  # run_id(meta.json)
    retr = _read_retrain(version_dir)               # git_commit(retrain.json — MJ1)
    with _LOCKS[fs]:                                # ── 임계 구간(결정 7-1) ──
        prev = deploy.active_version(fs)            # 구간 안 prev 읽기(디렉토리명, mn-c)
        deploy.swap(fs, version_dir, validation=val, approved=True)   # ② 미승인/REGRESSED면 raise
        ev = audit.append(event_type="APPROVE", featureset=fs,
                          gate_passed=bool(snap.get("no_regression")),
                          from_version=prev, to_version=version_id,    # 둘 다 디렉토리명(B1)
                          run_id=meta.get("run_id"), git_commit=retr.get("git_commit"),  # MJ1
                          gate_snapshot=snap, actor_unverified=actor, reason=reason)  # ③
    propagation = _propagate_and_confirm(fs)        # 전파는 구간 밖
    return {"event_id": ev.id, "prev": prev, "active": version_id, "propagation": propagation}


def rollback(fs: str, target_version_id: str, *, actor: str, reason: str = "") -> dict:
    _require_consistent(fs, target_version_id)      # 디렉토리명(B1)
    with _LOCKS[fs]:
        prev = deploy.active_version(fs)            # 롤백 prev = 사전 읽기(deploy.rollback이 prev 미반환, mn-c)
        # H4r 롤백 안전 게이트(BR2-1): deploy.rollback이 무게이트(set_alias만)라 백엔드에서 강제.
        # 프론트 archived 규칙과 동일한 _classify 재사용 — 롤백 대상은 '과거 활성 이력(archived)'이어야 한다.
        # 게이트를 validation 재검증이 아니라 과거활성 이력으로 건다(5-A 재검증 면제 유지). api.py가 ValueError→422.
        target_dir = _version_dir(target_version_id)
        ready = (target_dir / ".ready").exists()
        if _classify(target_version_id, active_id=prev,
                     past_active=_past_active_ids(fs), ready=ready) != "archived":
            raise ValueError(f"rollback target must be a past champion (archived): {target_version_id}")
        # archived(과거활성)여도 dir이 GC되면 dangling alias가 된다 — .ready 재검증은 면제(5-A)하되
        # dir 실재는 강제. 없으면 FileNotFoundError(→api 422). (MAJOR 결함 2)
        # GC는 버전 dir 전체를 제거하므로 dir 존재로 게이트(권위 = 디렉토리 실재).
        if not target_dir.is_dir():
            raise FileNotFoundError(
                f"rollback target dir missing/GC'd: {target_version_id}")
        deploy.rollback(fs, target_version_id, approved=True)   # 콘솔이 승인 경계(swap과 대칭). validation 재검증 면제 유지(5-A)
        ev = audit.append(event_type="ROLLBACK", featureset=fs, gate_passed=None,
                          from_version=prev, to_version=target_version_id,
                          actor_unverified=actor, reason=reason)
    propagation = _propagate_and_confirm(fs)
    return {"event_id": ev.id, "prev": prev, "active": target_version_id, "propagation": propagation}


# === 부트스트랩 화해/seed (결정 7-1·7-2) ===
def _reconcile_or_seed(fs: str) -> None:
    alias_target = deploy.active_version(fs)        # FS = 현재 활성 권위(디렉토리명 or None)
    last = audit.last_active(fs)
    if last is None and alias_target is not None:
        # 콜드스타트: 콘솔 이전부터 champion 존재 → seed 1건(mn1, actor=system)
        audit.append(event_type="BOOTSTRAP", featureset=fs, from_version=None,
                     to_version=alias_target, actor_unverified="system",
                     reason="cold-start seed")
    elif alias_target is not None and last is not None and last.to_version != alias_target:
        # ②후 ③전 크래시/콘솔 밖 수동 변경: 감사를 실제 alias로 끌어올림(양변 디렉토리명 → 거짓 RECONCILE 없음, B1)
        audit.append(event_type="RECONCILE", featureset=fs,
                     from_version=last.to_version,   # archived 도출 보존(mn-r5)
                     to_version=alias_target, actor_unverified="system",
                     reason="bootstrap reconcile")
    elif alias_target is None and last is not None:
        _alert_missing_alias(fs)                     # 심링크 소실 + 이력 존재: 거짓 복원 금지, 경보만(mn3)


def _alert_missing_alias(fs: str) -> None:
    _log.error("active alias missing for featureset %s (audit history exists)", fs)


@asynccontextmanager
async def lifespan(app):
    for fs in CONSOLE_FEATURESETS:                  # 라우팅 개시(yield) 전 화해/seed 완료(결정 7-1 경계 완전성)
        _reconcile_or_seed(fs)
    yield


# === 전파 확인 폴링 (결정 2-A) ===
def _trigger_reload(fs: str) -> None:
    # dev: POST /admin/reload | prod: K8s 롤링 재시작([검증 필요]). 테스트는 이 함수를 스텁.
    url = os.environ.get("SERVE_URL", "http://localhost:8000") + "/admin/reload"
    try:
        urllib.request.urlopen(urllib.request.Request(url, method="POST"), timeout=5)
    except Exception:  # noqa: BLE001  — 전파 실패는 폴링이 pending으로 드러냄
        pass


def _get_health(fs: str) -> dict:
    url = os.environ.get("SERVE_URL", "http://localhost:8000") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read())
    except Exception:  # noqa: BLE001
        return {}


def _propagate_and_confirm(fs: str, *, timeout_s: float = 10, interval_s: float = 0.5) -> str:
    _trigger_reload(fs)
    target = deploy.active_version(fs)              # ★ 타겟 = 현재 alias(그 swap의 버전 아님, MJ-r5)
    target_run_id = _read_meta(_version_dir(target)).get("run_id")   # 접두사 재부착 금지(B2)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _get_health(fs).get("run_id") == target_run_id:          # /health.run_id == 현재 alias run_id
            return "confirmed"
        time.sleep(interval_s)
    # 침묵 금지: serve가 시간 내 run_id를 확인 못 함 → 최소 로그로 표면화(능동 경보 채널은 후속 범위).
    _log.warning("propagation pending: serve did not confirm run_id for fs=%s within %ss",
                 fs, timeout_s)
    return "pending"                                # UI는 "전파 대기/실패"로 구분 표시


# === 읽기 — versions 분류/상세 (구현 2 보강 계약) ===
def _scan_version_ids(fs: str) -> list[str]:
    prefix = f"gru_{fs}@"
    if not ARTIFACTS.exists():
        return []
    return sorted(p.name for p in ARTIFACTS.iterdir() if p.is_dir() and p.name.startswith(prefix))


def _past_active_ids(fs: str) -> set[str]:
    # 감사 이력상 한때 활성이었던 버전(디렉토리명). to_version = APPROVE/ROLLBACK/RECONCILE/BOOTSTRAP 대상.
    return {ev.to_version for ev in audit.query(featureset=fs) if ev.to_version}


def _classify(version_id: str, *, active_id: str | None, past_active: set[str], ready: bool) -> str:
    # 상호배타, 우선순위 첫 매치: champion > archived > challenger > incomplete
    if version_id == active_id:
        return "champion"
    if version_id in past_active:                   # 과거 활성이면 .ready 무관 archived(challenger보다 우선)
        return "archived"
    if ready:
        return "challenger"
    return "incomplete"


def list_versions(fs: str) -> dict:
    active_id = deploy.active_version(fs)
    past_active = _past_active_ids(fs)
    rows = []
    for version_id in _scan_version_ids(fs):
        vdir = _version_dir(version_id)
        ready = (vdir / ".ready").exists()
        bucket = _classify(version_id, active_id=active_id, past_active=past_active, ready=ready)
        val = _read_validation(vdir)
        meta = _read_meta(vdir)
        rows.append({
            "version": _strip_prefix(fs, version_id),               # 응답 표면 = 맨버전(B2)
            "bucket": bucket,
            "ready": ready,
            "gate_passed": None if bucket == "incomplete" else val.get("no_regression"),
            "bholdout_util": val.get("bholdout_util"),
            "has_mlflow": bool(meta.get("run_id")),
        })
    return {"featureset": fs, "active": _strip_prefix(fs, active_id), "versions": rows}


def _mlflow_link(run_id: str | None) -> str | None:
    if not run_id or not MLFLOW_UI_BASE:            # run_id 없으면 죽은 링크 금지 → null(6-A 폴백)
        return None
    return f"{MLFLOW_UI_BASE}/#/experiments/0/runs/{run_id}"


def get_version_detail(fs: str, version: str) -> dict:
    # version = 응답 표면 맨버전(예: "champ"). 내부 식별자 = 디렉토리명으로 복원(단일 접두, 이중접두 아님).
    version_id = version if version.startswith(f"gru_{fs}@") else f"gru_{fs}@{version}"
    vdir = _version_dir(version_id)
    ready = (vdir / ".ready").exists()
    active_id = deploy.active_version(fs)
    bucket = _classify(version_id, active_id=active_id,
                       past_active=_past_active_ids(fs), ready=ready)
    meta_full = _read_meta(vdir)
    return {
        "version": _strip_prefix(fs, version_id),
        "bucket": bucket,
        "ready": ready,
        "gate": _read_validation(vdir),             # validation.json 통째
        "retrain": _read_retrain(vdir),             # retrain.json 통째
        "meta": {"featureset": meta_full.get("featureset"), "tau": meta_full.get("tau"),
                 "trained_on": meta_full.get("trained_on")},
        "mlflow_link": _mlflow_link(meta_full.get("run_id")),
    }
