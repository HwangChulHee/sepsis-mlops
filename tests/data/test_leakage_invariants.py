"""Hermetic 회귀 — 누수 방지 대원칙을 순수 함수 단위로 고정.

이 프로젝트의 논지는 "시험만 잘 보고 실전에 무너지는 가짜 성능"을 막는 것이고, 그 방어의
핵심 로직은 data/split·normalize·missing 의 순수 numpy/pandas 함수에 있다. 이들은 실
PhysioNet 데이터 없이 합성 배열/manifest 로 완전히 검증되는데, 그동안 hermetic 스위트에는
직접 테스트가 없었다(데이터 의존 스크립트로만 간접 검증). 여기서 불변을 직접 못박는다:

  · 환자 단위 분할 — 같은 pid 가 train/val/test 에 걸치지 않는다, B 는 봉인.
  · 0-fill 금지 — 결측만 train 평균으로, 진짜 0 은 0 그대로.
  · 미래 누수 없는 ffill — leading NaN 은 보존(과거→미래 방향만).
  · train-only 정규화 — 상수열도 0 division 없이 std=1 가드.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sepsis.data import missing, normalize
from sepsis.data import split as split_mod


# ============================== 결측 처리 (missing.py) ==============================
def test_ffill_preserves_leading_nan_and_carries_forward():
    # leading NaN 은 채울 과거가 없으므로 NaN 유지(미래값을 끌어오면 누수). 이후는 마지막 관측 carry.
    a = np.array([[np.nan], [1.0], [np.nan], [3.0], [np.nan]], dtype=np.float32)
    out = missing.ffill(a)
    assert np.isnan(out[0, 0]), "leading NaN 이 채워졌다 — 미래 누수"
    np.testing.assert_array_equal(out[1:, 0], np.array([1.0, 1.0, 3.0, 3.0], dtype=np.float32))


def test_ffill_columns_independent():
    a = np.array([[1.0, np.nan], [np.nan, 2.0], [np.nan, np.nan]], dtype=np.float32)
    out = missing.ffill(a)
    np.testing.assert_array_equal(out[:, 0], np.array([1.0, 1.0, 1.0], dtype=np.float32))
    assert np.isnan(out[0, 1])
    np.testing.assert_array_equal(out[1:, 1], np.array([2.0, 2.0], dtype=np.float32))


def test_fill_mean_is_not_zero_fill():
    # 핵심: 결측(NaN)만 train 평균으로. 진짜 0(예: 혈압 0)은 결측이 아니므로 0 그대로.
    a = np.array([[np.nan, 0.0], [5.0, np.nan]], dtype=np.float32)
    mean = np.array([10.0, 20.0], dtype=np.float32)
    out = missing.fill_mean(a, mean)
    assert out[0, 0] == 10.0, "NaN 이 평균으로 안 채워짐"
    assert out[0, 1] == 0.0, "진짜 0 이 결측처럼 덮였다 — 의료에서 0 은 실값"
    assert out[1, 0] == 5.0
    assert out[1, 1] == 20.0
    assert not np.any(np.isnan(out))


def test_missing_mask_polarity_from_raw():
    raw = np.array([[np.nan, 1.0], [2.0, np.nan]], dtype=np.float32)
    m = missing.missing_mask(raw)
    np.testing.assert_array_equal(m, np.array([[0, 1], [1, 0]], dtype=np.int8))


def test_compute_fill_mean_nanmean_over_train_hours():
    # 두 환자-시계열을 이어붙여 열별 nanmean(NaN 무시).
    p1 = np.array([[2.0, np.nan]], dtype=np.float32)
    p2 = np.array([[4.0, 10.0], [np.nan, 20.0]], dtype=np.float32)
    fm = missing.compute_fill_mean([p1, p2])
    np.testing.assert_allclose(fm, np.array([3.0, 15.0], dtype=np.float32))


# ============================== 정규화 (normalize.py) ==============================
def test_norm_stats_train_only_and_constant_col_guard():
    train = [np.array([[0.0, 5.0], [2.0, 5.0]], dtype=np.float32)]  # col1 상수(std=0)
    mean, std = normalize.compute_norm_stats(train)
    np.testing.assert_allclose(mean, np.array([1.0, 5.0], dtype=np.float32))
    assert std[0] == pytest.approx(1.0, abs=1e-4)   # std([0,2])=1
    assert std[1] == 1.0, "상수열 std=0 가드 실패 — normalize 에서 0 division"
    # 가드 덕에 normalize 가 NaN/inf 를 안 만든다.
    z = normalize.normalize(train[0], mean, std)
    assert np.all(np.isfinite(z))


def test_normalize_zscore():
    a = np.array([[10.0], [20.0]], dtype=np.float32)
    mean = np.array([15.0], dtype=np.float32)
    std = np.array([5.0], dtype=np.float32)
    z = normalize.normalize(a, mean, std)
    np.testing.assert_allclose(z, np.array([[-1.0], [1.0]], dtype=np.float32))


def test_clip_bounds_match_featureset_columns():
    # clip_bounds 는 featureset 열 순서대로 (lo, hi) 를 뽑는다 — 열/경계 정렬이 맞아야 한다.
    from sepsis import config as C
    lo, hi = normalize.clip_bounds("vitals")
    cols = C.featureset_columns("vitals")
    assert lo.shape == hi.shape == (len(cols),)
    assert np.all(hi >= lo), "clip 상한이 하한보다 작은 열이 있다"


def test_clip_applies_fixed_bounds():
    a = np.array([[-5.0, 100.0], [50.0, 300.0]], dtype=np.float32)
    lo = np.array([0.0, 0.0], dtype=np.float32)
    hi = np.array([40.0, 250.0], dtype=np.float32)
    out = normalize.clip(a, lo, hi)
    np.testing.assert_array_equal(out, np.array([[0.0, 100.0], [40.0, 250.0]], dtype=np.float32))


# ============================== 환자 단위 분할 (split.py) ==============================
def _manifest(n_a=10, n_b=5):
    pids = [f"p0{i:05d}" for i in range(n_a)] + [f"p1{i:05d}" for i in range(n_b)]
    site = ["training_setA"] * n_a + ["training_setB"] * n_b
    return pd.DataFrame({"pid": pids, "site": site})


def test_cross_site_patient_disjoint_and_b_sealed():
    m = _manifest(n_a=10, n_b=5)
    sp = split_mod.split_cross_site(m, val_frac=0.2, seed=42)
    a_all = set(m.loc[m.site == "training_setA", "pid"])
    b_all = set(m.loc[m.site == "training_setB", "pid"])
    train, val, b = set(sp["A_train"]), set(sp["A_val"]), set(sp["B"])

    assert train | val == a_all, "A_train ∪ A_val 이 setA 전체가 아님"
    assert train & val == set(), "환자가 A_train/A_val 에 걸침 — 누수"
    assert b == b_all, "B 가 setB 전체와 불일치"
    assert b & a_all == set(), "B(봉인)에 setA 환자가 섞임"
    assert (train | val) & b == set(), "B 환자가 학습/검증에 샘"
    assert len(val) == 2  # round(10*0.2)


def test_cross_site_val_size_uses_rounding_not_floor():
    # 뮤테이션 보강: 13*0.2=2.6 → round=3 (floor면 2). "반올림" 의미를 실제로 고정.
    # (n_a=10 입력만으론 round/floor 가 구별 안 돼 변이가 생존했었다.)
    m = _manifest(n_a=13, n_b=3)
    sp = split_mod.split_cross_site(m, val_frac=0.2, seed=1)
    assert len(sp["A_val"]) == 3, "A_val 크기가 round(13*0.2)=3 이 아님 (floor 로 새면 2)"
    assert len(sp["A_train"]) == 10


def test_cross_site_deterministic_by_seed():
    m = _manifest()
    assert split_mod.split_cross_site(m, seed=7) == split_mod.split_cross_site(m, seed=7)


def test_unified_three_way_disjoint_union():
    m = _manifest(n_a=9, n_b=6)  # 15 pid
    sp = split_mod.split_unified(m, val_frac=0.2, test_frac=0.2, seed=1)
    tr, va, te = set(sp["train"]), set(sp["val"]), set(sp["test"])
    assert tr | va | te == set(m["pid"]), "합집합이 전체 pid 가 아님"
    assert tr & va == set() and tr & te == set() and va & te == set(), "split 간 환자 중복 — 누수"
    assert len(te) == 3 and len(va) == 3 and len(tr) == 9


def test_train_split_name():
    assert split_mod.train_split_name("cross_site") == "A_train"
    assert split_mod.train_split_name("unified") == "train"
