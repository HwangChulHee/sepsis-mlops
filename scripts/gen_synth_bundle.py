"""구조적으로 유효한 합성 번들 생성기 — minikube 전파 사슬 E2E 검증용.

⚠️ 학습/실데이터 없음. 랜덤 초기화 GRUm2m state_dict + 임의(그러나 형상 정합) 통계로
serve가 *로드 가능한* 번들 구조만 만든다. 모델 성능 주장이 아니다(구조 검증 전용).

serve(load_bundle_from_dir) 가 요구하는 형식 + console(_classify·approve) 가 요구하는 메타:
  model.pt   = GRUm2m(input_dim=F, hidden, layers, dropout) state_dict (랜덤 가중치)
  pre.npz    = mu/sigma/fill_mean/clip_lo/clip_hi, 각 (F,) float32
  meta.json  = {featureset, input_dim=F, tau, hp{hidden,layers,dropout}, run_id, version, trained_on}
  reference.npz = drift Reference(summary (n,F)) — serve _load_all 가 calibrate 에 사용
  validation.json = ValidationResult 필드(+validated_at). no_regression 이 게이트(approve)를 가른다
  retrain.json = epochs·val_loss·seed·n_*·run_id·git_commit(MJ1 감사 출처)
  .ready     = 완성 표식(_classify 가 challenger/incomplete 가른다)

사용: **serve 파드** 안에서 실행(PVC /app/deploy/artifacts 에 기록). PYTHONPATH=/app/src.
  - console-api 이미지는 슬림화로 torch/numpy 를 들어냈으므로 이 생성기를 못 돌린다.
  - serve 이미지는 torch/numpy/pandas 가 있고 같은 공유 PVC 를 마운트하므로 여기서 실행한다.
  - serve 파드는 번들이 없으면 Ready 가 아니지만 Running 이라 `kubectl exec` 로 주입 가능(부트스트랩).
  예) kubectl cp scripts/gen_synth_bundle.py <serve-pod>:/tmp/gen.py && kubectl exec <serve-pod> -- python /tmp/gen.py
  python scripts/gen_synth_bundle.py
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np
import torch

from sepsis import config as C
from sepsis.train.gru import GRUm2m
from sepsis.drift.reference import Reference, save_reference

FEATURESET = "vitals"
ROOT = Path(os.environ.get("ARTIFACTS_DIR", "/app/deploy/artifacts"))
HP = {"hidden": 16, "layers": 1, "dropout": 0.0}


def _seed(label: str) -> int:
    """라벨 → 결정적 31비트 시드. 내장 hash()는 PYTHONHASHSEED 로 매 실행 달라져
    번들이 비재현이 된다 → sha256 으로 프로세스 무관 결정성 확보."""
    digest = hashlib.sha256(label.encode()).hexdigest()
    return int(digest, 16) % (2**31)


def make_bundle(label: str, *, no_regression: bool) -> str:
    cols = C.featureset_columns(FEATURESET)
    F = len(cols)
    version_id = f"gru_{FEATURESET}@{label}"
    d = ROOT / version_id
    d.mkdir(parents=True, exist_ok=True)
    run_id = f"synth-{label}-runid"

    # 1) model.pt — 랜덤 초기화 GRUm2m(학습 없음)
    torch.manual_seed(_seed(label))
    model = GRUm2m(F, HP["hidden"], HP["layers"], HP["dropout"])
    torch.save(model.state_dict(), d / "model.pt")

    # 2) pre.npz — 형상 정합 더미 통계(값은 임의, 형상만 (F,))
    np.savez(
        d / "pre.npz",
        mu=np.zeros(F, np.float32), sigma=np.ones(F, np.float32),
        fill_mean=np.zeros(F, np.float32),
        clip_lo=np.full(F, -10.0, np.float32), clip_hi=np.full(F, 10.0, np.float32),
    )

    # 3) meta.json
    (d / "meta.json").write_text(json.dumps({
        "featureset": FEATURESET, "input_dim": F, "tau": 0.5, "hp": HP,
        "run_id": run_id, "version": label, "trained_on": "SYNTHETIC (random init)",
    }, indent=2))

    # 4) reference.npz — 합성 Reference(랜덤 summary), serve calibrate 가 bootstrap 으로 사용
    rng = np.random.default_rng(_seed(label))
    summary = rng.normal(size=(200, F)).astype(np.float32)
    ref = Reference(
        featureset=FEATURESET, cols=cols, unit="patient_last", summary=summary,
        missing_rate=np.isnan(summary).mean(axis=0).astype(np.float32),
        low_card=np.zeros(F, dtype=bool), n_patients=summary.shape[0],
    )
    save_reference(ref, d / "reference.npz")

    # 5) validation.json — no_regression 이 approve 게이트를 가른다(REGRESSED 차단 테스트)
    (d / "validation.json").write_text(json.dumps({
        "no_regression": no_regression,
        "bholdout_util": 0.30 if no_regression else 0.10,
        "bholdout_prauc": 0.25, "new_aval_util": 0.28 if no_regression else 0.09,
        "old_aval_util": 0.25, "new_aval_prauc": 0.24, "old_aval_prauc": 0.23,
        "eps": 0.02, "cross_site_claim": False,  # 합성 — cross-site 주장 아님
        "distribution": {"ks": 0.01}, "note": "SYNTHETIC structural bundle",
        "validated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, indent=2))

    # 6) retrain.json — git_commit(MJ1 감사 출처)
    (d / "retrain.json").write_text(json.dumps({
        "epochs": 0, "val_loss": None, "b_split_seed": 42,
        "n_train_pids": 0, "n_b_retrain": 0, "n_b_holdout": 0,
        "run_id": run_id, "git_commit": "synthetic",
    }, indent=2))

    # 7) .ready — 완성 표식(마지막에 기록)
    (d / ".ready").write_text(json.dumps({"complete": True}))
    print(f"  made {version_id}  no_regression={no_regression}  run_id={run_id}")
    return version_id


def set_alias(target_version_id: str) -> None:
    """gru_<fs> -> target_version_id 상대 심링크(원자 교체)."""
    link = ROOT / f"gru_{FEATURESET}"
    tmp = ROOT / f"gru_{FEATURESET}.swap"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(target_version_id, tmp)            # 상대 타겟(같은 디렉토리)
    os.replace(tmp, link)
    print(f"  alias gru_{FEATURESET} -> {target_version_id}")


if __name__ == "__main__":
    print(f"ROOT={ROOT}")
    make_bundle("synthA", no_regression=True)    # 정상 champion(초기 활성)
    make_bundle("synthB", no_regression=False)   # REGRESSED — 게이트 차단 테스트
    make_bundle("synthC", no_regression=True)    # PASS — 승인→전파→synthA archived 유도
    set_alias("gru_vitals@synthA")               # 초기 champion seed
    print("done.")
