"""Hermetic — 서빙 FastAPI 라우트 계약(serve/app.py). ★ 배포 시 실제로 트래픽이 타는 경로.

기존 serve 테스트는 StatefulPredictor 유닛(test_predictor)·reload 오케스트레이션 대역
(console_prep)만 덮었고, **HTTP 라우트 계층 자체**(/predict·/health·/schema 의 요청→응답
계약)는 비어 있었다. 이 파일이 그 갭을 메운다.

핵심으로 고정하는 계약:
  · **결측 계약**(CLAUDE.md 누수 대원칙 "0으로 채우지 않음"): 없는 피처 OR 명시적 null 은
    np.nan 으로 예측기에 전달된다 — 절대 0/평균이 아니다(치료행동·사망 신호 오염 방지).
  · **키 한정**: cols 밖의 알 수 없는 피처가 오면 422 로 거부(조용히 무시하지 않음).
  · /predict·/health·/schema 응답 스키마.

실모델·MLflow·클러스터 없이 _S(state 스냅샷)를 직접 주입해 라우트만 격리한다 —
state() 는 _S 에 "pred" 가 있으면 로딩을 건너뛰므로 캘리브레이션도 타지 않는다.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

import sepsis.serve.app as serve_app
from sepsis import config as C

FS = "vitals"


class RecordingPredictor:
    """s['pred'].predict(pid, row) 대역 — 전달된 row 를 그대로 보관해 계약을 검사한다."""

    def __init__(self):
        self.calls = []  # list[(patient_id, np.ndarray)]

    def predict(self, patient_id, row):
        self.calls.append((patient_id, np.asarray(row)))
        return {"p": 0.42, "alarm": False}


@pytest.fixture
def ctx(monkeypatch):
    """_S 를 손으로 채워 라우트를 로딩 없이 격리. (pred, cols) 를 함께 돌려준다."""
    pred = RecordingPredictor()
    cols = C.featureset_columns(FS)
    bundle = SimpleNamespace(featureset=FS, run_id="testrun0000", input_dim=len(cols))
    monkeypatch.setattr(serve_app, "_S", {"bundle": bundle, "pred": pred, "cols": cols})
    return SimpleNamespace(client=TestClient(serve_app.app), pred=pred, cols=cols)


# ===== 결측 계약: 없는/null 피처 → NaN, 절대 0 아님 =====
def test_absent_and_null_features_become_nan_never_zero(ctx):
    """일부만 주고(Temp 없음), 하나는 명시적 null(SBP=None) → 그 자리는 NaN 이어야 한다.
    특히 0.0 이 아니어야 한다 — 0-fill 은 의료 누수(혈압 0=사망 등)."""
    sent = {"HR": 88.0, "O2Sat": 97.0, "SBP": None}  # Temp 등 나머지는 아예 없음
    r = ctx.client.post("/predict", json={"patient_id": "p1", "features": sent})
    assert r.status_code == 200, r.text

    assert len(ctx.pred.calls) == 1
    _, row = ctx.pred.calls[0]
    assert row.shape == (len(ctx.cols),)
    idx = {c: i for i, c in enumerate(ctx.cols)}

    # 준 값은 그대로
    assert row[idx["HR"]] == pytest.approx(88.0)
    assert row[idx["O2Sat"]] == pytest.approx(97.0)
    # 명시적 null → NaN (0 아님)
    assert math.isnan(row[idx["SBP"]])
    assert row[idx["SBP"]] != 0.0
    # 아예 없는 피처 → NaN (0 아님)
    assert math.isnan(row[idx["Temp"]])
    assert row[idx["Temp"]] != 0.0
    # 준 적 없는 나머지 전부 NaN — 어느 하나도 0 으로 채워지지 않음
    given = set(sent) - {"SBP"}
    for c in ctx.cols:
        if c not in given:
            assert math.isnan(row[idx[c]]), f"{c} 가 NaN 이 아니다(0-fill 회귀 의심)"


def test_predict_response_shape(ctx):
    r = ctx.client.post("/predict", json={"patient_id": "p2", "features": {"HR": 80.0}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"patient_id": "p2", "p": 0.42, "alarm": False, "featureset": FS}


# ===== 키 한정: 알 수 없는 피처 → 422 =====
def test_unknown_feature_rejected_422(ctx):
    r = ctx.client.post(
        "/predict",
        json={"patient_id": "p3", "features": {"HR": 80.0, "BOGUS": 1.0}},
    )
    assert r.status_code == 422, r.text
    assert "BOGUS" in r.text
    # 거부됐으므로 예측기까지 도달하지 않아야 한다(조용히 무시 금지)
    assert ctx.pred.calls == []


def test_empty_features_all_nan(ctx):
    """features={} 도 유효 — 모든 자리가 NaN(전부 결측인 첫 타임스텝)."""
    r = ctx.client.post("/predict", json={"patient_id": "p4", "features": {}})
    assert r.status_code == 200, r.text
    _, row = ctx.pred.calls[0]
    assert row.shape == (len(ctx.cols),)
    assert np.isnan(row).all()


# ===== /health · /schema 스키마 =====
def test_health_shape(ctx):
    body = ctx.client.get("/health").json()
    assert body == {"status": "ok", "run_id": "testrun0000",
                    "featureset": FS, "input_dim": len(ctx.cols)}


def test_schema_shape(ctx):
    body = ctx.client.get("/schema").json()
    assert body == {"featureset": FS, "features": ctx.cols, "n_features": len(ctx.cols)}


# ===== _row_from 순수함수 직접(라우트 우회, 계약의 최소 단위) =====
def test_row_from_contract_direct():
    cols = C.featureset_columns(FS)
    row = serve_app._row_from({"HR": 70.0, "MAP": None}, cols)
    assert row.dtype == np.float32
    idx = {c: i for i, c in enumerate(cols)}
    assert row[idx["HR"]] == pytest.approx(70.0)
    assert math.isnan(row[idx["MAP"]])          # null → NaN
    assert math.isnan(row[idx["Temp"]])         # 없음 → NaN
    assert not np.any(row == 0.0)               # 어디에도 0-fill 없음


def test_row_from_unknown_raises_422():
    from fastapi import HTTPException
    cols = C.featureset_columns(FS)
    with pytest.raises(HTTPException) as ei:
        serve_app._row_from({"NOPE": 1.0}, cols)
    assert ei.value.status_code == 422
