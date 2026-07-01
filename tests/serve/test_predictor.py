"""Hermetic — 상태 유지 예측기(serve/predictor). 환자별 hidden state·alarm 임계.

StatefulPredictor 는 환자별 hidden state 를 carry 하고 타임스텝당 1회 전진한다. 가짜
model(forward_state)·가짜 Bundle 로 torch 실모델 없이 계약을 고정한다:
  · alarm = p >= tau (경계).
  · hidden state 를 환자별로 이어받는다(같은 pid 는 직전 h 를 다음 호출에 전달).
  · 환자 간 state 격리, reset 으로 초기화.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import torch

from sepsis.serve.predictor import StatefulPredictor


class FakeModel:
    """forward_state(x, h) -> (logit, h_n). logit 고정, h 는 호출마다 +1 로 carry 추적."""

    def __init__(self, logit: float):
        self.logit = logit
        self.hs_seen: list = []            # 각 호출이 받은 h (None 시작 → 이후 carry 값)

    def forward_state(self, x, h=None):
        self.hs_seen.append(h)
        new_h = (0 if h is None else h) + 1
        return torch.tensor([[[self.logit]]], dtype=torch.float32), new_h


def _bundle(model, tau):
    # F=1, 항등 전처리(fill0·clip 무한·mu0/sig1) → step 이 row 를 그대로 통과.
    f32 = lambda a: np.array(a, dtype=np.float32)  # noqa: E731
    return SimpleNamespace(input_dim=1, model=model, tau=tau,
                           fill_mean=f32([0]), clip_lo=f32([-1e9]), clip_hi=f32([1e9]),
                           mu=f32([0]), sigma=f32([1]))


def _row():
    return np.array([1.0], dtype=np.float32)


def test_alarm_true_when_p_ge_tau():
    # logit 0 → p=sigmoid(0)=0.5. tau=0.5 → alarm True(경계 포함, >=).
    pred = StatefulPredictor(_bundle(FakeModel(0.0), tau=0.5))
    out = pred.predict("p1", _row())
    assert out["p"] == 0.5 and out["alarm"] is True


def test_alarm_false_when_p_below_tau():
    pred = StatefulPredictor(_bundle(FakeModel(0.0), tau=0.6))  # 0.5 < 0.6
    assert pred.predict("p1", _row())["alarm"] is False


def test_hidden_state_carried_within_patient():
    m = FakeModel(0.0)
    pred = StatefulPredictor(_bundle(m, tau=0.5))
    pred.predict("p1", _row())          # h: None → 저장 1
    pred.predict("p1", _row())          # h: 1 전달
    pred.predict("p1", _row())          # h: 2 전달
    assert m.hs_seen == [None, 1, 2], f"환자 내 hidden state 미전달: {m.hs_seen}"


def test_state_isolated_across_patients():
    m = FakeModel(0.0)
    pred = StatefulPredictor(_bundle(m, tau=0.5))
    pred.predict("A", _row())           # A: None
    pred.predict("B", _row())           # B: None(독립) — A 의 h 를 받으면 안 됨
    assert m.hs_seen == [None, None]


def test_reset_clears_hidden_state():
    m = FakeModel(0.0)
    pred = StatefulPredictor(_bundle(m, tau=0.5))
    pred.predict("A", _row())           # None → 저장 1
    pred.reset("A")
    pred.predict("A", _row())           # 리셋 후 다시 None 시작
    assert m.hs_seen == [None, None]
