"""회귀 — MLflow deep-link 이 실험 id 를 하드코딩하지 않는다(D-retrain#1).

retrain 런은 experiment "retrain"(id≠0)에 사는데 링크가 experiments/0 으로 박히면 소속을
검증하는 MLflow UI 에서 404. MLFLOW_EXPERIMENT_ID 로 파라미터화됨을 고정한다.
"""
from __future__ import annotations

import sepsis.console.service as svc


def test_link_uses_configurable_experiment_id(monkeypatch):
    monkeypatch.setattr(svc, "MLFLOW_UI_BASE", "http://mlflow.local")
    monkeypatch.setattr(svc, "MLFLOW_EXPERIMENT_ID", "7")
    link = svc._mlflow_link("abc123")
    assert link == "http://mlflow.local/#/experiments/7/runs/abc123"


def test_link_null_when_base_unset(monkeypatch):
    monkeypatch.setattr(svc, "MLFLOW_UI_BASE", None)
    assert svc._mlflow_link("abc123") is None


def test_link_null_when_run_id_missing(monkeypatch):
    monkeypatch.setattr(svc, "MLFLOW_UI_BASE", "http://mlflow.local")
    assert svc._mlflow_link(None) is None
