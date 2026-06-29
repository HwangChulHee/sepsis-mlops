"""서빙 reload 오케스트레이션을 구현 내부(실모델 로딩) 없이 검증하기 위한 헬퍼.

핸드오프 구현 4 가 명세한 _load_all 의 **오케스트레이션 계약**을 테스트한다:
  _load_all(version_dir) 가
    - load_bundle_from_dir(version_dir)            ← serve.app 네임스페이스
    - R.load_reference(version_dir/"reference.npz") ← sepsis.drift.reference.load_reference
    - synthetic.calibrate(ref, window_n=, n_trials=) ← sepsis.drift.synthetic.calibrate
    - StatefulPredictor(bundle)                     ← serve.app 네임스페이스
  를 **동일 version_dir** 로 호출하고, _S·_DS 를 원자 재바인딩한다.

이 심볼들을 대역(sentinel)으로 패치하면 실제 model.pt/reference.npz 없이도
스큐 없음·스키마·인자타입·재바인딩 계약을 직접 검증할 수 있다.
src/ 구현 코드는 읽지 않았다 — 핸드오프가 적시한 호출 그래프만 신뢰한다.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import sepsis.drift.reference as drift_reference
import sepsis.drift.synthetic as drift_synthetic
import sepsis.serve.app as serve_app

REF_SENTINEL = SimpleNamespace(tag="REF_SENTINEL")
THR_SENTINEL = SimpleNamespace(tag="THR_SENTINEL")


class Records:
    def __init__(self):
        self.bundle_dirs = []
        self.ref_paths = []
        self.calibrate_calls = []  # list[(ref, window_n, n_trials)]


def patch_loaders(monkeypatch, *, run_id_default="hashrunid0000000"):
    """_load_all 이 부르는 4개 심볼을 대역으로 교체하고 _S·_DS 를 비운다."""
    rec = Records()

    def fake_load_bundle_from_dir(version_dir):
        rec.bundle_dirs.append(os.fspath(version_dir))
        # bundle.run_id 은 meta.json.run_id 폴백(bundle.py:102 계약)을 에뮬레이션:
        # version_dir/meta.json 이 있으면 그 run_id, 없으면 기본값.
        run_id = run_id_default
        meta_p = os.path.join(os.fspath(version_dir), "meta.json")
        if os.path.exists(meta_p):
            run_id = json.loads(open(meta_p).read()).get("run_id", run_id)
        return SimpleNamespace(featureset="vitals", run_id=run_id)

    def fake_load_reference(path):
        rec.ref_paths.append(os.fspath(path))
        return REF_SENTINEL

    def fake_calibrate(ref, window_n=None, n_trials=None):
        rec.calibrate_calls.append((ref, window_n, n_trials))
        return THR_SENTINEL

    def fake_stateful_predictor(bundle):
        return SimpleNamespace(kind="PRED", bundle=bundle)

    monkeypatch.setattr(serve_app, "load_bundle_from_dir", fake_load_bundle_from_dir)
    monkeypatch.setattr(serve_app, "StatefulPredictor", fake_stateful_predictor)
    monkeypatch.setattr(drift_reference, "load_reference", fake_load_reference)
    monkeypatch.setattr(drift_synthetic, "calibrate", fake_calibrate)
    # 리더가 잡을 수 있는 옛 스냅샷 제거 — 매 테스트 깨끗한 lazy 상태
    monkeypatch.setattr(serve_app, "_S", {})
    monkeypatch.setattr(serve_app, "_DS", {})
    return rec


def setup_alias(tmp_path, fs, version_dir):
    """ARTIFACTS/gru_<fs> → version_dir symlink 구성 후 serve_app.ARTIFACTS 패치."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(exist_ok=True)
    version_dir.mkdir(parents=True, exist_ok=True)
    alias = artifacts / f"gru_{fs}"
    if alias.exists() or alias.is_symlink():
        alias.unlink()
    os.symlink(version_dir, alias)
    return artifacts, alias
