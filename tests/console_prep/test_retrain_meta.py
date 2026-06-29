"""성공기준 1 — 재학습 메타 3종 주입 (handoff '성공 기준' #1, 결정 3).

검증 대상(handoff:207):
- RetrainResult 에 run_id·git_commit·seed 가 채워진다.
- seed 가 retrain.json.b_split_seed 에 도달한다(MJ1) → test_persist_atomic.py 에서 영속 측면 교차검증.
- 재학습 run 이 sqlite:///{C.ROOT}/mlflow.db 에 기록돼 run_id 로 조회된다(MJ-a).
- git_commit 은 더티 트리에 '+dirty', non-repo 에 'unknown'(mn1).

src/ 구현 코드는 읽지 않았다. 핸드오프가 명세한 심볼만 import 한다.
"""
from __future__ import annotations

import dataclasses
import subprocess

import pytest

from sepsis import config as C
from sepsis.retrain import pipeline
from sepsis.retrain.pipeline import RetrainResult


# --- 1a: RetrainResult 에 메타 3종 필드 존재 + 기본값(handoff:23-27) ---
def test_retrainresult_has_meta_fields():
    # 성공기준 1
    fields = {f.name: f for f in dataclasses.fields(RetrainResult)}
    for name in ("run_id", "git_commit", "seed"):
        assert name in fields, f"RetrainResult 에 {name} 필드가 없다(구현1 미반영)"


def test_retrainresult_meta_defaults():
    # 성공기준 1 — handoff:23-27 의 기본값(run_id=''·git_commit=''·seed=0)
    fields = {f.name: f for f in dataclasses.fields(RetrainResult)}
    assert fields["run_id"].default == ""
    assert fields["git_commit"].default == ""
    assert fields["seed"].default == 0


# --- 1d: _git_commit() 헬퍼 — dirty / clean / non-repo (mn1, handoff:48-56) ---
def _fake_run_factory(head_sha=None, porcelain="", git_missing=False, head_fails=False):
    """subprocess.run 대역 — 호출 args 로 rev-parse / status 분기."""
    def fake_run(args, *a, **k):
        is_rev_parse = "rev-parse" in args
        is_status = "status" in args
        if is_rev_parse:
            if git_missing:
                raise FileNotFoundError("git not found")
            if head_fails:
                raise subprocess.CalledProcessError(128, args)
            return subprocess.CompletedProcess(args, 0, stdout=head_sha + "\n", stderr="")
        if is_status:
            return subprocess.CompletedProcess(args, 0, stdout=porcelain, stderr="")
        raise AssertionError(f"예상치 못한 subprocess 호출: {args}")
    return fake_run


def test_git_commit_clean_tree_returns_plain_sha(monkeypatch):
    # 성공기준 1 (mn1) — 워킹트리 clean 이면 sha 그대로(+dirty 없음)
    monkeypatch.setattr(pipeline.subprocess, "run",
                        _fake_run_factory(head_sha="abc1234", porcelain=""))
    assert pipeline._git_commit() == "abc1234"


def test_git_commit_dirty_tree_appends_dirty(monkeypatch):
    # 성공기준 1 (mn1) — 더티 트리면 '+dirty' 접미사
    monkeypatch.setattr(pipeline.subprocess, "run",
                        _fake_run_factory(head_sha="abc1234",
                                          porcelain=" M src/foo.py\n"))
    assert pipeline._git_commit() == "abc1234+dirty"


def test_git_commit_non_repo_returns_unknown(monkeypatch):
    # 성공기준 1 (mn1) — git 부재/non-repo 면 'unknown'
    monkeypatch.setattr(pipeline.subprocess, "run",
                        _fake_run_factory(git_missing=True))
    assert pipeline._git_commit() == "unknown"


def test_git_commit_head_fails_returns_unknown(monkeypatch):
    # 성공기준 1 (mn1) — rev-parse 실패(CalledProcessError)도 'unknown'
    monkeypatch.setattr(pipeline.subprocess, "run",
                        _fake_run_factory(head_fails=True))
    assert pipeline._git_commit() == "unknown"


# --- 1c: MLflow run 이 sqlite 스토어에 기록되고 run_id 로 조회 가능 (MJ-a) ---
def test_retrain_experiment_registered_in_sqlite_store():
    # 성공기준 1 (MJ-a)
    # [검증 필요] 선행: 구현1(retrain() 를 mlflow.start_run 으로 감싸고
    #   set_tracking_uri(f"sqlite:///{C.ROOT}/mlflow.db")·set_experiment("retrain"))이
    #   구현되고, 재학습이 최소 1회 실행돼 'retrain' experiment 에 run 이 남아야 의미가 있다.
    # 핸드오프(handoff:34-35)가 못 박은 단일 스토어/experiment 계약을 직접 검증:
    #   콘솔 deep-link 가 run_id 로 조회하려면 run 이 이 sqlite 스토어에 있어야 한다.
    import mlflow
    from mlflow.tracking import MlflowClient

    mlflow.set_tracking_uri(f"sqlite:///{C.ROOT}/mlflow.db")
    client = MlflowClient(tracking_uri=f"sqlite:///{C.ROOT}/mlflow.db")
    exp = client.get_experiment_by_name("retrain")
    assert exp is not None, "재학습 experiment 'retrain' 이 sqlite 스토어에 없다(구현1 미반영 또는 미실행)"

    runs = client.search_runs([exp.experiment_id], max_results=1)
    assert runs, "'retrain' experiment 에 run 이 없다 — 재학습이 MLflow run 으로 기록되지 않았다"
    r = runs[0]
    # 핸드오프 log_params/log_metrics 계약(handoff:40-41)
    assert "seed" in r.data.params and "featureset" in r.data.params
    assert "epochs" in r.data.metrics and "val_loss" in r.data.metrics
    # run_id 로 단건 조회 가능(콘솔 deep-link 해석 — experiment 무관)
    assert mlflow.get_run(r.info.run_id).info.run_id == r.info.run_id
