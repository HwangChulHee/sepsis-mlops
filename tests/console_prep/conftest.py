"""공유 픽스처/헬퍼 — console-prep H4 백엔드 보강 TDD.

출처(이 셋만 신뢰): design/console-prep/handoff.md(명세부, 주 출처),
decisions.md(결정 1~7), handoff_review.md(확정 계약).
src/ 구현 코드는 일절 읽지 않았다 — 핸드오프가 명세한 심볼/시그니처/필드를 그대로 신뢰해
import·구성한다. 구현이 없으니 지금은 RED 가 정상이다.

핸드오프가 못 박은 인터페이스(신뢰 근거):
- RetrainResult 필드(handoff:10): featureset·input_dim·hp·tau·stats·model·b_retrain·
  b_holdout·train_pids·aval_raw·bholdout_data·aval_data·epochs·val_loss·mask_on
  + (구현1 추가) run_id·git_commit·seed
- ValidationResult 필드(handoff:12): bholdout_util·bholdout_prauc·new_aval_util·
  old_aval_util·new_aval_prauc·old_aval_prauc·no_regression·cross_site_claim·
  distribution·note + (구현3-pre 추가) eps(default 0.02)
- materialize(retrain_result, version, *, validation, root=ARTIFACTS)  (handoff:89)
  → version dir = root/f"gru_{rr.featureset}@{version}" 에
    model.pt·pre.npz·meta.json·reference.npz + validation.json·retrain.json·.ready 기록
"""
from __future__ import annotations

import numpy as np
import pytest

# --- 핸드오프가 명세한 심볼(이름/경로를 그대로 신뢰) ---
from sepsis import config as C
from sepsis.retrain import deploy
from sepsis.retrain.pipeline import RetrainResult
from sepsis.retrain.validate import ValidationResult


# ===== 외부-데이터 의존 격리 (materialize 의 reference 빌드 단계만 우회) =====
# materialize 는 JSON 영속(validation.json·retrain.json·.ready) **앞에서**
# sepsis.drift.reference.build_reference_from_pids(featureset, train_pids, pid2site)
# 를 호출한다. 이 함수는 실제 manifest 의 pid(load_feats_labels)를 요구해서,
# 본 테스트의 가짜 pid(p001 등)로는 KeyError 가 난다(pytest 트레이스백으로 확인).
#
# 이 reference 빌드는 **외부 데이터 의존**이지, 우리가 검증하려는 JSON 영속 계약의
# 일부가 아니다. 따라서 reference 빌드/저장 단계만 가짜로 우회하고, 그 뒤의 JSON
# 영속 로직(validation.json·retrain.json·.ready·meta.json·pre.npz·model.pt)은
# **진짜로 타게** 둔다 — 그래야 13건 계약이 실제로 검증된다.
#
# autouse 전역: materialized_dir 픽스처를 경유하지 않고 본문에서 직접
# deploy.materialize(...) 를 부르는 테스트(order·eps·leakage 등)도 *본문을 건드리지
# 않고* 격리하려면 패키지 전역 autouse 여야 한다. build_reference_from_pids 를
# 타지 않는 serve/drift 테스트(자체 patch_loaders 사용)에는 무영향이다.
class _RefSentinel:
    """build_reference_from_pids 가뜨려 돌려주는 가짜 reference (내용 미단언)."""
    pass


@pytest.fixture(autouse=True)
def _isolate_reference_build(monkeypatch):
    import sepsis.drift.reference as R

    # (1) 시작점: 실제 manifest pid 를 읽는 reference 빌드를 sentinel 로 우회.
    def _fake_build_reference_from_pids(featureset, pids, pid2site):
        return _RefSentinel()

    monkeypatch.setattr(R, "build_reference_from_pids",
                        _fake_build_reference_from_pids, raising=False)

    # (2) 저장: sentinel 은 실제 reference.npz 를 만들지 못하니, 저장 함수가
    #     플레이스홀더 파일만 쓰게 우회(어떤 테스트도 reference.npz 내용은 단언하지 않음).
    #     함수가 존재할 때만 패치(raising=False). 저장 시그니처는 (reference, path) 가정.
    def _fake_save_reference(ref, path):
        import pathlib
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")  # 더미 플레이스홀더

    for _name in ("save_reference", "save", "write_reference", "to_npz", "save_npz"):
        if hasattr(R, _name):
            monkeypatch.setattr(R, _name, _fake_save_reference, raising=False)

    yield


def make_validation(**over):
    """ValidationResult 를 모든 문서화 필드로 구성(handoff:12 + 구현3-pre eps)."""
    base = dict(
        bholdout_util=0.42,
        bholdout_prauc=0.31,
        new_aval_util=0.40,
        old_aval_util=0.38,
        new_aval_prauc=0.30,
        old_aval_prauc=0.28,
        no_regression=True,
        cross_site_claim="A->B holds",
        distribution={"ks": 0.01},
        note="ok",
        eps=0.02,  # 구현3-pre: 게이트에 실제 사용된 eps 의 단일 출처
    )
    base.update(over)
    return ValidationResult(**base)


def make_retrain(**over):
    """RetrainResult 를 모든 문서화 필드로 구성(handoff:10 + 구현1 메타 3종).

    model 은 실제 torch 모듈(materialize 의 model.pt 기록 통과용),
    pid 3그룹은 **교집합 없는 disjoint** 로 둔다(누수 불변 — 환자 단위 분할).
    """
    import torch

    input_dim = 4
    # [검증 필요] 선행: materialize 의 기존 model.pt/pre.npz/reference.npz 기록부가
    # 아래 타입(torch.nn.Module, dict[str,np.ndarray], np.ndarray)을 그대로 수용한다고 가정.
    # 수용 형태가 다르면 RED 로 드러나며, 이는 핸드오프가 그 내부 계약을 명시하지 않았다는 신호다.
    base = dict(
        featureset="vitals",
        input_dim=input_dim,
        hp={"lr": 1e-3, "hidden": 8, "epochs": 2},
        tau=0.5,
        stats={"mean": np.array([1.0, 2.0, 3.0, 4.0]),
               "std": np.array([5.0, 6.0, 7.0, 8.0])},
        model=torch.nn.Linear(input_dim, 1),
        b_retrain=["p100", "p101", "p102"],
        b_holdout=["p200", "p201"],
        train_pids=["p001", "p002", "p003", "p004"],
        aval_raw=np.zeros((6, input_dim), dtype=np.float32),
        bholdout_data=np.zeros((5, input_dim), dtype=np.float32),
        aval_data=np.zeros((6, input_dim), dtype=np.float32),
        epochs=2,
        val_loss=0.1234,
        mask_on=False,  # 누수 불변: 마스크 기본 OFF
        run_id="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",  # 32-hex 형태(alias 명이 아님)
        git_commit="deadbeef",
        seed=42,
    )
    base.update(over)
    return RetrainResult(**base)


@pytest.fixture
def rr():
    return make_retrain()


@pytest.fixture
def vr():
    return make_validation()


@pytest.fixture
def materialized_dir(tmp_path, rr, vr):
    """게이트 통과(no_regression=True) 버전을 materialize 한 version dir.

    [검증 필요] 선행: 구현 3(validation.json·retrain.json·.ready 영속) + 기존
    model.pt/pre.npz/meta.json/reference.npz 기록부가 make_retrain 의 스텁을 수용.
    """
    out = deploy.materialize(rr, "v1-retrain", validation=vr, root=tmp_path)
    return out


@pytest.fixture
def regressed_materialized_dir(tmp_path):
    """게이트 실패(no_regression=False) 버전도 materialize 돼야 한다(MJ-b, 성공기준 4)."""
    rr = make_retrain(run_id="f00df00df00df00df00df00df00df00d")
    vr = make_validation(no_regression=False)
    out = deploy.materialize(rr, "v1-regressed", validation=vr, root=tmp_path)
    return out
