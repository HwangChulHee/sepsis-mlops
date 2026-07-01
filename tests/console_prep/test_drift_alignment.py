"""성공기준 7 — drift 정합(B1): /drift baseline = 활성 alias 번들의 reference.npz.

검증 대상(handoff:213, 결정 5·6, B1):
- /drift 가 /predict 보다 먼저 와도 drift_state() 가 활성 alias 번들의 reference.npz 를
  baseline 으로 쓴다(SERVE_BUNDLE_DIR/build_reference 옛 경로 회귀 없음, B1-3).
- _load_all 은 새 reference 로 thr 를 재캘리브레이션한다(B1-c).
- R.load_reference 에 reference.npz **파일** 경로가 전달된다(디렉토리 아님, B1-2).

src/ 구현 코드는 읽지 않았다. handoff 구현 4(b)(c)(d) 의 호출 그래프만 검증한다.
"""
from __future__ import annotations

import os
from pathlib import Path

from _serve_helpers import REF_SENTINEL, THR_SENTINEL, patch_loaders, setup_alias

import sepsis.serve.app as serve_app


def test_drift_first_loads_via_alias_not_serve_bundle_dir(tmp_path, monkeypatch):
    # 성공기준 7 (B1-3) — /drift 가 먼저 와도 alias 경로로 _load_all, baseline = alias 번들 reference
    decoy = tmp_path / "decoy_bundle_dir"   # 옛 SERVE_BUNDLE_DIR 경로(회귀하면 여기서 읽음)
    decoy.mkdir()
    monkeypatch.setenv("SERVE_BUNDLE_DIR", os.fspath(decoy))  # 설정돼도 무시돼야 함(superseded)

    vdir = tmp_path / "gru_vitals@v1-retrain"
    artifacts, _ = setup_alias(tmp_path, "vitals", vdir)
    monkeypatch.setattr(serve_app, "ARTIFACTS", artifacts)
    monkeypatch.setenv("SERVE_FEATURESET", "vitals")
    rec = patch_loaders(monkeypatch)

    ds = serve_app.drift_state()       # /predict 없이 /drift 가 먼저
    assert set(ds.keys()) == {"ref", "thr", "min_patients"}
    assert ds["ref"] is REF_SENTINEL
    # baseline reference 는 alias version_dir 의 reference.npz — decoy 가 아님
    assert Path(rec.ref_paths[-1]).resolve() == (vdir / "reference.npz").resolve()
    assert os.fspath(decoy) not in rec.ref_paths
    assert all(os.fspath(decoy) not in b for b in rec.bundle_dirs)


def test_load_reference_receives_npz_file_path_not_dir(tmp_path, monkeypatch):
    # 성공기준 7 (B1-2) — R.load_reference 인자는 .npz 파일 경로(디렉토리 넘기면 즉시 예외)
    vdir = tmp_path / "gru_vitals@v1-retrain"
    vdir.mkdir(parents=True)
    rec = patch_loaders(monkeypatch)

    serve_app._load_all(vdir)
    assert rec.ref_paths, "R.load_reference 가 호출되지 않았다"
    last = Path(rec.ref_paths[-1])
    assert last.name == "reference.npz", f"파일이 아닌 경로 전달: {last}"
    assert os.fspath(last) != os.fspath(vdir)


def test_drift_recalibrates_thr_on_each_load(tmp_path, monkeypatch):
    # 성공기준 7 (B1-c) — 매 _load_all 이 새 reference 로 thr 재캘리브(옛 thr 재사용 금지)
    vdir = tmp_path / "gru_vitals@v1-retrain"
    vdir.mkdir(parents=True)
    rec = patch_loaders(monkeypatch)

    serve_app._load_all(vdir)
    assert serve_app._DS["thr"] is THR_SENTINEL
    ref, _, _ = rec.calibrate_calls[-1]
    assert ref is REF_SENTINEL  # 캘리브 입력 = 방금 로드한 새 reference


def test_drift_state_and_state_route_to_same_version_dir(tmp_path, monkeypatch):
    # 성공기준 7/6 — state()·drift_state() 모두 동일 alias→version_dir 로 라우팅(모델↔reference 동일 버전)
    vdir = tmp_path / "gru_vitals@v1-retrain"
    artifacts, _ = setup_alias(tmp_path, "vitals", vdir)
    monkeypatch.setattr(serve_app, "ARTIFACTS", artifacts)
    monkeypatch.setenv("SERVE_FEATURESET", "vitals")
    rec = patch_loaders(monkeypatch)

    serve_app.drift_state()
    serve_app.state()
    # 두 진입점이 부른 모든 bundle/reference 로드가 같은 version_dir 산
    for b in rec.bundle_dirs:
        assert Path(b).resolve() == vdir.resolve()
    for p in rec.ref_paths:
        assert Path(p).resolve() == (vdir / "reference.npz").resolve()
