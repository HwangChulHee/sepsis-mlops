"""회귀 — 검출 표본수 = 보정 표본수(D#2).

synthetic.calibrate 는 window_n 표본의 H0 분산으로 임계를 잡는다. run.analyze 가 그보다 큰
window 전체에 임계를 적용하면 H0 분산이 작아져 과소검출한다. analyze 는 검출 입력을 가장
최근 window_n 환자로 잘라야 한다 — synthetic.detect 에 넘어가는 current 의 행수로 고정한다.
"""
from __future__ import annotations

import numpy as np

from sepsis.drift import run as run_mod
from sepsis.drift import synthetic
from sepsis.drift.reference import Reference


def _make_reference(n=400, F=4, seed=0):
    rng = np.random.default_rng(seed)
    summary = rng.normal(size=(n, F)).astype(np.float32)
    return Reference(
        featureset="vitals",
        cols=[f"c{j}" for j in range(F)],
        unit="patient_last",
        summary=summary,
        missing_rate=np.zeros(F, dtype=np.float32),
        low_card=np.zeros(F, dtype=bool),
        n_patients=n,
    )


class _FakeWindow:
    """patient_summary()가 window_n 보다 많은 환자를 돌려주는 window 대역."""

    def __init__(self, summary):
        self._summary = summary

    def ready(self, min_patients):
        return self._summary.shape[0] >= min_patients

    def patient_summary(self):
        return self._summary


def test_analyze_caps_detection_sample_to_window_n(monkeypatch):
    window_n = 50
    ref = _make_reference()
    thr = synthetic.calibrate(ref, window_n=window_n, n_trials=20, seed=1)

    # window 에 window_n 의 5배 환자를 넣는다.
    rng = np.random.default_rng(2)
    big = rng.normal(size=(window_n * 5, ref.summary.shape[1])).astype(np.float32)
    window = _FakeWindow(big)

    seen = {}
    real_detect = synthetic.detect

    def spy_detect(reference, current, thresholds):
        seen["n"] = current.shape[0]
        return real_detect(reference, current, thresholds)

    monkeypatch.setattr(run_mod.synthetic, "detect", spy_detect)
    monkeypatch.setattr(run_mod.watch, "publish", lambda det: None)
    monkeypatch.setattr(run_mod.watch, "publish_insufficient", lambda: None)

    det = run_mod.analyze(ref, window, thr, min_patients=window_n)
    assert det is not None
    assert seen["n"] == window_n, (
        f"검출 표본수 {seen['n']} != 보정 표본수 {window_n} — analyze 가 window 를 안 잘랐다"
    )


def test_analyze_uses_most_recent_window_n_rows(monkeypatch):
    """자를 때 '가장 최근'(뒤쪽) window_n 행을 쓴다(슬라이딩 윈도우)."""
    window_n = 10
    ref = _make_reference(F=3)
    thr = synthetic.calibrate(ref, window_n=window_n, n_trials=10, seed=1)

    # 행마다 고유 마커(첫 열=순번) — 뒤쪽 window_n 이 왔는지 값으로 확인.
    total = 30
    marked = np.zeros((total, 3), dtype=np.float32)
    marked[:, 0] = np.arange(total)
    window = _FakeWindow(marked)

    captured = {}

    def spy_detect(r, cur, t):
        captured["cur"] = cur
        return []

    monkeypatch.setattr(run_mod.synthetic, "detect", spy_detect)
    monkeypatch.setattr(run_mod.watch, "publish", lambda det: None)
    monkeypatch.setattr(run_mod.watch, "publish_insufficient", lambda: None)

    run_mod.analyze(ref, window, thr, min_patients=window_n)
    cur = captured["cur"]
    assert cur.shape[0] == window_n
    assert np.array_equal(cur[:, 0], np.arange(total - window_n, total))
