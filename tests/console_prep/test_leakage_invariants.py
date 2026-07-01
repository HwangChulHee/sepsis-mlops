"""성공기준 8 — 누수 불변(영속·로딩 계층 한정 "do no harm").

검증 대상(handoff:214, 성공 기준 #8 / decisions:123):
  "위 변경(영속·로딩·메타데이터)이 환자 단위 B 분할·train-only stats·0-fill 금지·
   ICULOS 제외·mask OFF 를 건드리지 않는다."

주의(번호 정렬): 지시문은 누수 불변을 '성공기준 #7' 로 불렀으나, handoff.md '성공 기준'
섹션의 실제 번호는 **#8**(드리프트 정합이 #7)이다. 본 파일은 출처 문서(handoff)의 번호를 따른다.

이 작업의 범위는 *영속/로딩 계층*이다(handoff:5 대상 파일). 따라서 여기서는 그 계층이
누수 대원칙을 **새로 깨지 않는지**(do no harm)를 박는다. 분할/정규화 *계산* 자체의
정당성은 H1/H2 단계 테스트의 몫이며, 본 작업이 건드리지 않음을 전제로 한다.

src/ 구현 코드는 읽지 않았다. CLAUDE.md 누수 방지 대원칙 + handoff 계약만 신뢰한다.
"""
from __future__ import annotations

import json

import numpy as np
from conftest import make_validation

from sepsis import config as C
from sepsis.retrain import deploy


# ===== ICULOS 제외 — 우측 절단 시간 단서가 서빙 피처에 들어오면 안 됨 =====
def test_iculos_excluded_from_served_featureset():
    # 성공기준 8 — ICULOS 제외(CLAUDE.md 대원칙)
    cols = list(C.featureset_columns("vitals"))
    assert not any("iculos" in str(c).lower() for c in cols), \
        f"ICULOS 가 서빙 피처에 포함됨(시간 누수): {cols}"


# ===== 마스크 기본 OFF — 치료행동 누수 통로(research/04) =====
def test_mask_off_by_default_in_served_columns():
    # 성공기준 8 — 결측 마스크 기본 OFF(옵트인)
    cols = list(C.featureset_columns("vitals"))
    assert not any("mask" in str(c).lower() for c in cols), \
        f"마스크 채널이 기본 피처에 포함됨(마스크 OFF 위반): {cols}"


# ===== train-only stats 보존 + 0-fill 금지 — 영속 계층이 stats 를 재계산/0채움 하지 않음 =====
def test_materialize_persists_stats_verbatim_no_recompute(tmp_path, rr):
    # 성공기준 8 — pre.npz 는 RetrainResult.stats(= train split 에서만 산출)를 그대로 영속.
    # 영속 계층이 stats 를 0 으로 덮거나 재계산하면 train-only/0-fill 불변이 깨진다.
    # [검증 필요] 선행: materialize 의 기존 pre.npz 기록부가 np.savez 로 stats dict 키를 보존.
    out = deploy.materialize(rr, "v-stats", validation=make_validation(), root=tmp_path)
    pre = np.load(out / "pre.npz")
    assert "mean" in pre.files and "std" in pre.files, \
        f"pre.npz 가 train-only stats 키를 보존하지 않음: {pre.files}"
    np.testing.assert_allclose(pre["mean"], rr.stats["mean"])
    np.testing.assert_allclose(pre["std"], rr.stats["std"])
    # 0-fill 금지: 영속된 stats 가 통째로 0 으로 덮이지 않았다
    assert not np.allclose(pre["mean"], 0.0)


# ===== 환자 단위 분할 보존 — 영속 계층이 split 경계를 병합/누수시키지 않음 =====
def test_persistence_preserves_disjoint_patient_split_counts(tmp_path, rr):
    # 성공기준 8 — train/retrain/holdout 환자 집합은 서로소(같은 환자가 split 에 걸치지 않음).
    # 영속된 카운트가 입력 disjoint 집합 크기와 일치 → 영속 계층이 환자를 섞지 않는다.
    train, retr, hold = set(rr.train_pids), set(rr.b_retrain), set(rr.b_holdout)
    # 픽스처 불변: 세 집합은 서로소여야 한다(누수 대원칙)
    assert train.isdisjoint(retr) and train.isdisjoint(hold) and retr.isdisjoint(hold)

    out = deploy.materialize(rr, "v-split", validation=make_validation(), root=tmp_path)
    rj = json.loads((out / "retrain.json").read_text())
    assert rj["n_train_pids"] == len(train)
    assert rj["n_b_retrain"] == len(retr)
    assert rj["n_b_holdout"] == len(hold)


# ===== mask OFF 메타 — 재학습 결과가 mask_on=False 를 영속(마스크 옵트인 미선택) =====
def test_default_retrain_keeps_mask_on_false(rr):
    # 성공기준 8 — 기본 재학습은 mask_on=False(마스크 OFF 옵트인 대원칙)
    assert rr.mask_on is False
