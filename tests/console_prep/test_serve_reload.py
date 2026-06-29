"""성공기준 5·6 — 서빙 alias reload + _S/_DS 동일 버전 원자 재바인딩.

검증 대상(handoff:211-212, 결정 5·6):
- #5: state() 가 alias(gru_<fs>)를 해석해 로드. ARTIFACTS 기본값 = C.ROOT/deploy/artifacts
      **절대경로**(MJ-e). /admin/reload 후 새 활성 버전 반영.
- #6: _S·_DS 가 동일 version_dir 에서 로드(스큐 없음). _DS 스키마 = ref·thr·min_patients
      (drift_endpoint 가 읽는 그대로, B1-1). 로드는 _LOCK 직렬화 + 원자 재바인딩(MJ-d).
- 교차: /health.run_id 가 alias명이 아닌 실제 run_id(성공기준 2, MJ2).

src/ 구현 코드는 읽지 않았다. handoff 가 명세한 호출 그래프만 대역으로 검증한다.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from sepsis import config as C
import sepsis.serve.app as serve_app
from _serve_helpers import patch_loaders, setup_alias, REF_SENTINEL, THR_SENTINEL


# ===== 5: ARTIFACTS 기본값 절대경로 (MJ-e, handoff:134) =====
def test_artifacts_default_is_absolute_and_matches_deploy():
    # 성공기준 5 (MJ-e) — 환경변수 미설정 시 C.ROOT/deploy/artifacts 절대경로
    if "ARTIFACTS_DIR" in os.environ:
        pytest.skip("ARTIFACTS_DIR 환경변수가 설정돼 기본값 검증 불가")
    assert os.path.isabs(os.fspath(serve_app.ARTIFACTS)), "ARTIFACTS 가 상대경로(cwd 의존) — MJ-e 위반"
    assert Path(serve_app.ARTIFACTS) == Path(C.ROOT) / "deploy" / "artifacts"


# ===== 5: _resolve_alias 가 alias symlink 를 version dir 로 해석 =====
def test_resolve_alias_follows_symlink(tmp_path, monkeypatch):
    # 성공기준 5
    vdir = tmp_path / "gru_vitals@v1-retrain"
    artifacts, _ = setup_alias(tmp_path, "vitals", vdir)
    monkeypatch.setattr(serve_app, "ARTIFACTS", artifacts)
    resolved = serve_app._resolve_alias("vitals")
    assert Path(resolved).resolve() == vdir.resolve()


# ===== 5/6: state() 가 alias 해석 → _load_all → _S 구성 =====
def test_state_loads_bundle_via_alias(tmp_path, monkeypatch):
    # 성공기준 5·6
    vdir = tmp_path / "gru_vitals@v1-retrain"
    artifacts, _ = setup_alias(tmp_path, "vitals", vdir)
    monkeypatch.setattr(serve_app, "ARTIFACTS", artifacts)
    monkeypatch.setenv("SERVE_FEATURESET", "vitals")
    rec = patch_loaders(monkeypatch)

    s = serve_app.state()
    assert {"bundle", "pred", "cols"} <= set(s.keys())
    # alias 가 가리키는 version_dir 로 번들을 로드했다
    assert rec.bundle_dirs, "load_bundle_from_dir 가 호출되지 않았다(alias 미해석)"
    assert Path(rec.bundle_dirs[-1]).resolve() == vdir.resolve()


# ===== 6: _S·_DS 동일 version_dir + _DS 스키마(ref·thr·min_patients) =====
def test_load_all_S_and_DS_from_same_version_dir(tmp_path, monkeypatch):
    # 성공기준 6 (B1-1, 스큐 없음)
    vdir = tmp_path / "gru_vitals@v1-retrain"
    vdir.mkdir(parents=True)
    rec = patch_loaders(monkeypatch)

    serve_app._load_all(vdir)
    # _DS 스키마는 정확히 ref·thr·min_patients (drift_endpoint 계약, app.py:118·120·121)
    assert set(serve_app._DS.keys()) == {"ref", "thr", "min_patients"}
    assert serve_app._DS["ref"] is REF_SENTINEL
    assert serve_app._DS["thr"] is THR_SENTINEL
    # 번들과 reference 가 동일 version_dir 산
    assert Path(rec.bundle_dirs[-1]).resolve() == vdir.resolve()
    assert Path(rec.ref_paths[-1]).resolve() == (vdir / "reference.npz").resolve()


def test_load_all_calibrates_with_default_window_and_trials(tmp_path, monkeypatch):
    # 성공기준 6/7 — thr 재캘리브: window_n=500·n_trials=300 기본(handoff:157-158)
    vdir = tmp_path / "gru_vitals@v1-retrain"
    vdir.mkdir(parents=True)
    monkeypatch.delenv("DRIFT_WINDOW_N", raising=False)
    monkeypatch.delenv("DRIFT_CAL_TRIALS", raising=False)
    rec = patch_loaders(monkeypatch)

    serve_app._load_all(vdir)
    assert rec.calibrate_calls, "synthetic.calibrate 가 호출되지 않았다(thr 재캘리브 누락)"
    ref, window_n, n_trials = rec.calibrate_calls[-1]
    assert ref is REF_SENTINEL  # 새 reference 로 재캘리브(옛 thr 재사용 금지, B1-c)
    assert window_n == 500
    assert n_trials == 300
    assert serve_app._DS["min_patients"] == 500


# ===== 6: 원자 재바인딩 — 리더가 잡은 옛 스냅샷이 변이되지 않는다 (MJ-d) =====
def test_atomic_rebind_old_snapshot_immutable(tmp_path, monkeypatch):
    # 성공기준 6 (MJ-d) — clear()/update() 2단계가 아니라 새 dict 1회 재바인딩.
    v1 = tmp_path / "gru_vitals@v1"
    v2 = tmp_path / "gru_vitals@v2"
    v1.mkdir(parents=True)
    v2.mkdir(parents=True)
    patch_loaders(monkeypatch)

    serve_app._load_all(v1)
    old_s = serve_app._S           # 리더가 잡은 스냅샷
    old_ds = serve_app._DS
    serve_app._load_all(v2)        # 두 번째 로드 → 재바인딩
    # 옛 dict 객체는 변이되지 않았다(완전 스냅샷). 새 _S/_DS 는 다른 객체.
    assert serve_app._S is not old_s
    assert serve_app._DS is not old_ds
    assert old_s.keys() and old_ds.keys()  # 옛 스냅샷이 비워지지(clear) 않았다


def test_lock_exists_and_is_a_lock():
    # 성공기준 6 — 로드 직렬화용 _LOCK 존재
    assert hasattr(serve_app, "_LOCK")
    assert hasattr(serve_app._LOCK, "acquire") and hasattr(serve_app._LOCK, "release")


# ===== 5: /admin/reload 후 새 활성 버전 반영 =====
def test_admin_reload_returns_active_version_dir(tmp_path, monkeypatch):
    # 성공기준 5 — POST /admin/reload → {"reloaded": True, "version_dir": "gru_vitals@..."}
    from fastapi.testclient import TestClient

    vdir = tmp_path / "gru_vitals@v1-retrain"
    artifacts, _ = setup_alias(tmp_path, "vitals", vdir)
    monkeypatch.setattr(serve_app, "ARTIFACTS", artifacts)
    monkeypatch.setenv("SERVE_FEATURESET", "vitals")
    patch_loaders(monkeypatch)

    client = TestClient(serve_app.app)
    r = client.post("/admin/reload")
    assert r.status_code == 200
    body = r.json()
    assert body["reloaded"] is True
    assert body["version_dir"] == "gru_vitals@v1-retrain"


# ===== 2: /health.run_id = 실제 run_id(alias명 아님) (MJ2) =====
def test_health_returns_real_run_id_not_alias(tmp_path, monkeypatch):
    # 성공기준 2 (MJ2)
    # [검증 필요] 선행: 구현2(meta.json.run_id) + load_bundle_from_dir 의
    #   meta.get("run_id", d.name) 폴백(bundle.py:102)으로 bundle.run_id 가 meta 에서 온다.
    #   여기선 그 폴백 계약을 대역으로 에뮬레이션(meta.json.run_id 를 그대로 사용).
    import json
    from fastapi.testclient import TestClient

    real_run_id = "abcdef0123456789abcdef0123456789"
    vdir = tmp_path / "gru_vitals@v1-retrain"
    artifacts, _ = setup_alias(tmp_path, "vitals", vdir)
    (vdir / "meta.json").write_text(json.dumps({"run_id": real_run_id}))
    monkeypatch.setattr(serve_app, "ARTIFACTS", artifacts)
    monkeypatch.setenv("SERVE_FEATURESET", "vitals")
    patch_loaders(monkeypatch)

    client = TestClient(serve_app.app)
    r = client.get("/health")
    assert r.status_code == 200
    run_id = r.json().get("run_id")
    assert run_id == real_run_id
    assert run_id != "gru_vitals", "/health.run_id 가 alias명으로 오염(MJ2 위반)"
