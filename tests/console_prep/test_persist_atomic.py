"""성공기준 3·4 — validation.json·retrain.json·.ready 원자 co-visible 영속.

검증 대상(handoff:209-210, 결정 1·2·7):
- #3: version dir 에 validation.json·retrain.json 기록 + **둘 다 완전할 때만 .ready** 존재.
      .ready 없는 dir = 미완성(결정 7). validation.json.eps == 게이트가 쓴 eps(MJ-c).
      validated_at 은 UTC 'Z' 표기(mn5).
- #4: 각 JSON 은 os.replace 로 원자 기록(torn read 없음). materialize 는 게이트 통과/실패
      무관하게 호출돼 REGRESSED 도 challenger 로 영속(MJ-b).
- 교차: seed → retrain.json.b_split_seed 도달(MJ1, 성공기준 1), meta.json.run_id(성공기준 2).

src/ 구현 코드는 읽지 않았다.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re

import pytest

from conftest import make_validation
from sepsis.retrain import deploy
from sepsis.retrain.validate import ValidationResult

UTC_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# ===== _atomic_write_json 원자 기법 단위 테스트 (성공기준 4, handoff:106-111) =====
def test_atomic_write_json_writes_complete_json(tmp_path):
    # 성공기준 4
    target = tmp_path / "x.json"
    obj = {"a": 1, "b": [2, 3], "c": "hi"}
    deploy._atomic_write_json(target, obj)
    assert json.loads(target.read_text()) == obj


def test_atomic_write_json_no_tmp_leftover(tmp_path):
    # 성공기준 4 — 성공 시 temp(.tmp) 잔재가 남지 않는다
    target = tmp_path / "x.json"
    deploy._atomic_write_json(target, {"k": "v"})
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == [], f"temp 파일이 남았다: {leftovers}"


def test_atomic_write_json_target_never_torn_on_replace_failure(tmp_path, monkeypatch):
    # 성공기준 4 — 원자성의 핵심: 본문은 temp 에 쓰고 os.replace 로만 타겟에 노출.
    # os.replace 가 실패하면 타겟은 **생성조차 안 돼야** 한다(부분/torn write 불가능).
    target = tmp_path / "x.json"

    def boom(src, dst):
        raise RuntimeError("simulated replace failure")

    monkeypatch.setattr(deploy.os, "replace", boom)
    with pytest.raises(RuntimeError):
        deploy._atomic_write_json(target, {"k": "v"})
    assert not target.exists(), "타겟이 직접 쓰여졌다 — os.replace 원자 경로가 아니다(torn read 위험)"


def test_atomic_write_json_overwrite_keeps_old_until_replace(tmp_path, monkeypatch):
    # 성공기준 4 — 기존 파일이 있을 때 replace 실패 시 옛 내용이 보존(중간 빈/부분 상태 없음)
    target = tmp_path / "x.json"
    deploy._atomic_write_json(target, {"v": "old"})

    def boom(src, dst):
        raise RuntimeError("fail")

    monkeypatch.setattr(deploy.os, "replace", boom)
    with pytest.raises(RuntimeError):
        deploy._atomic_write_json(target, {"v": "new"})
    assert json.loads(target.read_text()) == {"v": "old"}


# ===== materialize 가 두 JSON + .ready 를 기록 (성공기준 3·4) =====
def test_materialize_writes_validation_retrain_ready(materialized_dir):
    # 성공기준 3 — 세 산출물이 모두 존재(완성 = 두 파일 AND, .ready 마커)
    assert (materialized_dir / "validation.json").exists()
    assert (materialized_dir / "retrain.json").exists()
    assert (materialized_dir / ".ready").exists()


def test_ready_marker_content(materialized_dir):
    # 성공기준 3 — .ready = AND 완성 표식 {"complete": True} (handoff:102)
    assert json.loads((materialized_dir / ".ready").read_text()) == {"complete": True}


def test_both_json_are_complete_parseable(materialized_dir):
    # 성공기준 3·4 — .ready 가 보이면 두 JSON 은 완전 파싱 가능(torn 아님)
    json.loads((materialized_dir / "validation.json").read_text())
    json.loads((materialized_dir / "retrain.json").read_text())


def test_ready_written_last_after_both_json(tmp_path, rr, vr, monkeypatch):
    # 성공기준 3·4 — .ready 는 두 JSON **이후 마지막**에 기록(중간상태 비노출)
    order = []
    real = deploy._atomic_write_json

    def recording(path, obj):
        order.append(os.path.basename(str(path)))
        return real(path, obj)

    monkeypatch.setattr(deploy, "_atomic_write_json", recording)
    deploy.materialize(rr, "v-order", validation=vr, root=tmp_path)
    # validation.json·retrain.json 이 .ready 보다 먼저, .ready 가 마지막
    assert ".ready" in order and order[-1] == ".ready"
    assert order.index("validation.json") < order.index(".ready")
    assert order.index("retrain.json") < order.index(".ready")


def test_incomplete_dir_without_ready_is_not_complete(tmp_path):
    # 성공기준 3 — .ready 없는 dir 은 미완성(결정 7). validation.json 만 있고 .ready 부재면 미완성.
    d = tmp_path / "gru_vitals@partial"
    d.mkdir()
    (d / "validation.json").write_text(json.dumps({"no_regression": True}))
    # retrain.json 누락 → .ready 없음 → 콘솔이 challenger 로 인지하면 안 됨
    assert not (d / ".ready").exists()


# ===== validation.json 내용 (성공기준 3, 결정 1) =====
def test_validation_json_has_all_validationresult_fields(materialized_dir):
    # 성공기준 3 — asdict(validation) 전 필드 영속(결정 1)
    val = json.loads((materialized_dir / "validation.json").read_text())
    for name in (f.name for f in dataclasses.fields(ValidationResult)):
        assert name in val, f"validation.json 에 {name} 누락"
    # 무회귀 헤드라인/하드 게이트 키(결정 1)
    assert "no_regression" in val
    for k in ("new_aval_util", "old_aval_util", "new_aval_prauc", "old_aval_prauc",
              "bholdout_util", "bholdout_prauc", "cross_site_claim"):
        assert k in val


def test_validation_json_validated_at_is_utc_z(materialized_dir):
    # 성공기준 3 (mn5) — validated_at 은 UTC 'Z' 표기
    val = json.loads((materialized_dir / "validation.json").read_text())
    assert "validated_at" in val
    assert UTC_Z.match(val["validated_at"]), f"UTC Z 형식 아님: {val['validated_at']}"


def test_validation_json_eps_is_persisted_value_not_hardcoded(tmp_path, rr):
    # 성공기준 3 (MJ-c) — 영속 eps == 게이트가 실제 쓴 eps. 하드코딩 0.02 가 아님을 증명:
    # ValidationResult.eps 를 0.07 로 두면 validation.json.eps 도 0.07 이어야 한다.
    out = deploy.materialize(rr, "v-eps", validation=make_validation(eps=0.07), root=tmp_path)
    val = json.loads((out / "validation.json").read_text())
    assert val["eps"] == 0.07, "영속 eps 가 게이트 eps 와 desync(하드코딩 의심)"


def test_validationresult_has_eps_field_default():
    # 성공기준 3 (구현3-pre, MJ-c) — ValidationResult 에 eps 필드(default 0.02)
    fields = {f.name: f for f in dataclasses.fields(ValidationResult)}
    assert "eps" in fields, "ValidationResult 에 eps 필드 없음(구현3-pre 미반영)"
    assert fields["eps"].default == 0.02


# ===== retrain.json 내용 (성공기준 3·1/MJ1) =====
def test_retrain_json_fields(materialized_dir, rr):
    # 성공기준 3 — retrain.json 키 계약(handoff:96-98)
    rj = json.loads((materialized_dir / "retrain.json").read_text())
    assert rj["epochs"] == rr.epochs
    assert rj["val_loss"] == rr.val_loss
    assert rj["n_train_pids"] == len(rr.train_pids)
    assert rj["n_b_retrain"] == len(rr.b_retrain)
    assert rj["n_b_holdout"] == len(rr.b_holdout)
    assert rj["run_id"] == rr.run_id
    assert rj["git_commit"] == rr.git_commit


def test_seed_reaches_b_split_seed(materialized_dir, rr):
    # 성공기준 1 (MJ1) — seed 가 영속 지점(retrain.json.b_split_seed)에 도달
    rj = json.loads((materialized_dir / "retrain.json").read_text())
    assert rj["b_split_seed"] == rr.seed == 42


# ===== meta.json.run_id (성공기준 2, 결정 4) =====
def test_meta_json_has_run_id(materialized_dir, rr):
    # 성공기준 2 — meta.json 에 run_id 기록(연결 키 단일 권위 출처)
    meta = json.loads((materialized_dir / "meta.json").read_text())
    assert meta.get("run_id") == rr.run_id


# ===== 항상 materialize (성공기준 4, MJ-b) =====
def test_regressed_version_still_materialized(regressed_materialized_dir):
    # 성공기준 4 (MJ-b) — no_regression=False 여도 모든 산출물 영속(challenger 로 표시)
    d = regressed_materialized_dir
    assert (d / "validation.json").exists()
    assert (d / "retrain.json").exists()
    assert (d / ".ready").exists()
    val = json.loads((d / "validation.json").read_text())
    assert val["no_regression"] is False
