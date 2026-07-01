"""Hermetic — 스트리밍 전처리(serve/preprocess_rt). train-serving skew·0-fill 금지 가드.

StreamPreprocessor 는 환자별 ffill carry 후 fill_mean→clip→z-score 를 학습과 동일 함수·
동결 A 상수로 적용한다. 가짜 Bundle(상수만)로 정확한 파이프라인·상태 격리를 고정한다:
  · 결측은 train 평균으로 채운다(0 아님) — 의료 skew 방지.
  · leading 결측도 0 아니라 평균.
  · ffill 이 환자별로 마지막 관측을 carry.
  · 환자 간 상태 격리, reset 으로 초기화, shape 가드.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from sepsis.serve.preprocess_rt import StreamPreprocessor


def _bundle(F, *, fill_mean, clip_lo, clip_hi, mu, sigma):
    # 실제 Bundle 이 아니어도 step 이 읽는 필드만 있으면 된다(input_dim·상수 5종).
    f32 = lambda a: np.array(a, dtype=np.float32)  # noqa: E731
    return SimpleNamespace(input_dim=F, fill_mean=f32(fill_mean),
                           clip_lo=f32(clip_lo), clip_hi=f32(clip_hi),
                           mu=f32(mu), sigma=f32(sigma))


def test_missing_filled_with_train_mean_not_zero():
    # F=2, 전부 결측 → fill_mean[10,20] 으로 채움(0-fill 이면 0 이 나와 실패). mu0/sig1 → 그대로.
    b = _bundle(2, fill_mean=[10, 20], clip_lo=[-1e9, -1e9], clip_hi=[1e9, 1e9],
                mu=[0, 0], sigma=[1, 1])
    out = StreamPreprocessor(b).step("p1", np.array([np.nan, np.nan], dtype=np.float32))
    np.testing.assert_allclose(out, [10.0, 20.0])
    assert not np.any(out == 0.0), "결측이 0 으로 채워졌다 — train-serving skew"


def test_full_pipeline_exact_values():
    # ffill→fill_mean→clip→z-score 순서·상수 적용을 정확한 수치로 고정.
    # row=[100,nan]: obs[0] → state=[100,50(fill)] → clip[0..10],[0..100]=[10,50]
    #   → z=( (10-5)/1, (50-50)/10 )=[5,0].
    b = _bundle(2, fill_mean=[0, 50], clip_lo=[0, 0], clip_hi=[10, 100],
                mu=[5, 50], sigma=[1, 10])
    out = StreamPreprocessor(b).step("p1", np.array([100.0, np.nan], dtype=np.float32))
    np.testing.assert_allclose(out, [5.0, 0.0], atol=1e-6)


def test_ffill_carries_last_observed_across_steps():
    b = _bundle(2, fill_mean=[0, 0], clip_lo=[-1e9, -1e9], clip_hi=[1e9, 1e9],
                mu=[0, 0], sigma=[1, 1])
    pre = StreamPreprocessor(b)
    pre.step("p1", np.array([5.0, np.nan], dtype=np.float32))     # state=[5, fill0]
    out = pre.step("p1", np.array([np.nan, 7.0], dtype=np.float32))  # carry 5, set 7
    np.testing.assert_allclose(out, [5.0, 7.0])


def test_per_patient_state_isolated():
    b = _bundle(1, fill_mean=[0], clip_lo=[-1e9], clip_hi=[1e9], mu=[0], sigma=[1])
    pre = StreamPreprocessor(b)
    pre.step("A", np.array([9.0], dtype=np.float32))              # A carries 9
    out_b = pre.step("B", np.array([np.nan], dtype=np.float32))   # B 는 독립 → fill 0
    np.testing.assert_allclose(out_b, [0.0])


def test_reset_clears_state():
    b = _bundle(1, fill_mean=[0], clip_lo=[-1e9], clip_hi=[1e9], mu=[0], sigma=[1])
    pre = StreamPreprocessor(b)
    pre.step("A", np.array([9.0], dtype=np.float32))
    pre.reset("A")
    out = pre.step("A", np.array([np.nan], dtype=np.float32))     # 리셋 후 carry 없음 → fill 0
    np.testing.assert_allclose(out, [0.0])


def test_wrong_shape_raises():
    b = _bundle(2, fill_mean=[0, 0], clip_lo=[0, 0], clip_hi=[1, 1], mu=[0, 0], sigma=[1, 1])
    with pytest.raises(ValueError):
        StreamPreprocessor(b).step("A", np.array([1.0], dtype=np.float32))  # (1,) != (2,)
