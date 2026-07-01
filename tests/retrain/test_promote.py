"""Hermetic — 승격 권고(retrain/promote.py). ★ 드리프트 지속성 기반 investigate/none 결정.

promote() 는 재학습/배포를 트리거하지 않고 사람에게 권고만 한다(성능은 지연·umbrella 편향이라
보조). 규약: dataset_drift_share 가 share_threshold 를 **trailing 연속** persistence 사이클 이상
초과하면 "investigate". 경계(strict >, 연속성 리셋)를 결정 테이블로 고정한다.
"""
from __future__ import annotations

from sepsis.retrain.promote import promote


def _cycle(share: float, drifted: list[str] | None = None) -> dict:
    feats = [{"feature": f, "drift": True} for f in (drifted or [])]
    return {"dataset_drift_share": share, "features": feats}


def test_empty_detections_is_none():
    r = promote([])
    assert r.action == "none"
    assert r.dataset_drift_share == 0.0
    assert r.persisted_cycles == 0
    assert "no drift analysis windows" in r.reason


def test_single_spike_below_persistence_is_none():
    # 1회 초과, persistence=2 → 아직 none(일회성 스파이크 흡수).
    r = promote([_cycle(0.5)], persistence=2)
    assert r.action == "none"
    assert r.persisted_cycles == 1


def test_two_consecutive_above_triggers_investigate():
    r = promote([_cycle(0.4), _cycle(0.5)], persistence=2)
    assert r.action == "investigate"
    assert r.persisted_cycles == 2


def test_three_consecutive_counts_all():
    r = promote([_cycle(0.4), _cycle(0.6), _cycle(0.5)], persistence=2)
    assert r.action == "investigate"
    assert r.persisted_cycles == 3


def test_persistence_resets_on_dip():
    # 초과 → 하강 → 초과: trailing 연속은 마지막 1개뿐 → none(연속성 리셋).
    r = promote([_cycle(0.5), _cycle(0.1), _cycle(0.5)], persistence=2)
    assert r.action == "none"
    assert r.persisted_cycles == 1


def test_threshold_is_strict_greater_than():
    # share == threshold(0.3)는 초과가 아니다(strict >) → 카운트 안 됨.
    r = promote([_cycle(0.3), _cycle(0.3)], share_threshold=0.3, persistence=2)
    assert r.action == "none"
    assert r.persisted_cycles == 0


def test_custom_threshold_and_persistence():
    # threshold=0.5, persistence=3: 0.6 세 번 연속만 발동.
    dets = [_cycle(0.6), _cycle(0.6), _cycle(0.6)]
    assert promote(dets, share_threshold=0.5, persistence=3).action == "investigate"
    assert promote(dets[:2], share_threshold=0.5, persistence=3).action == "none"


def test_drifted_features_from_latest_only():
    r = promote([_cycle(0.4, ["HR"]), _cycle(0.5, ["HR", "Resp"])], persistence=2)
    assert r.action == "investigate"
    assert r.drifted_features == ["HR", "Resp"]        # 최신 사이클 기준
    assert r.dataset_drift_share == 0.5
