"""Hermetic — τ 선택·적용(eval/threshold). 평가 코어라 정직성 직결.

공식 utility(eval/utility) 위에서 τ 를 A-val 로 고르고 얼리는 로직. 순수 함수라 합성
(labels, probs)로 정확한 불변을 고정한다:
  · τ 가 최대확률보다 크면 = 전부 음성예측 = 무행동(inaction) → 정규화 utility 정확히 0.
  · probs = best_predictions 면 = utility-최적 예측 → 정규화 utility 정확히 1.
  · select_threshold 의 (τ*, norm*) 는 utility_at(τ*) 와 일치(두 함수 정합).
  · 양성 없는 코호트 → denom 0 → NaN(오승격 방지).
"""
from __future__ import annotations

import math

import numpy as np

from sepsis.eval import threshold
from sepsis.eval import utility as U

# 패혈증 환자(첫 양성 idx 8) + 비패혈증 환자(전부 0) 한 코호트.
SEPTIC = np.array([0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1], dtype=np.int8)
NONSEPTIC = np.array([0] * 10, dtype=np.int8)


def _cohort():
    labels = [SEPTIC, NONSEPTIC]
    # probs = 각 환자의 utility-최적 예측(0/1) 을 float 로 — τ=0.5 에서 best 를 재현.
    probs = [U.best_predictions(l).astype(np.float64) for l in labels]
    return labels, probs


def test_utility_at_beyond_max_prob_is_inaction_zero():
    labels, probs = _cohort()
    # τ=1.1 > 모든 prob → 전부 음성예측 = 무행동 기준 그 자체 → 정규화 utility 정확히 0.
    assert threshold.utility_at(labels, probs, tau=1.1) == 0.0


def test_utility_at_perfect_prediction_is_one():
    labels, probs = _cohort()
    # probs=best → τ=0.5 에서 예측이 best 와 동일 → 정규화 utility 정확히 1.
    assert threshold.utility_at(labels, probs, tau=0.5) == 1.0


def test_select_threshold_consistent_with_utility_at():
    labels, probs = _cohort()
    tau_star, norm_star = threshold.select_threshold(labels, probs)
    # select 가 돌려준 τ* 로 utility_at 을 다시 재면 같은 값이어야 한다(두 함수 정합).
    assert threshold.utility_at(labels, probs, tau_star) == norm_star
    # best 를 재현하는 probs 이므로 최적 정규화 utility 는 1.0 도달 가능.
    assert norm_star == 1.0


def test_select_threshold_is_a_maximum_over_grid():
    labels, probs = _cohort()
    _, norm_star = threshold.select_threshold(labels, probs)
    # 임의 τ 의 utility_at 이 최댓값(norm_star)을 넘을 수 없다.
    for tau in (0.0, 0.25, 0.5, 0.75, 1.0):
        assert threshold.utility_at(labels, probs, tau) <= norm_star + 1e-12


def test_all_negative_cohort_is_nan_not_crash():
    # 양성 0 → u_best==u_in → denom 0 → NaN(오승격 유발 안 함), select 는 (0.5, nan).
    labels = [NONSEPTIC, np.array([0, 0, 0], dtype=np.int8)]
    probs = [np.array([0.9] * 10), np.array([0.1, 0.2, 0.3])]
    assert math.isnan(threshold.utility_at(labels, probs, tau=0.5))
    tau, norm = threshold.select_threshold(labels, probs)
    assert tau == 0.5 and math.isnan(norm)
