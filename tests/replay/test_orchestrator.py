"""리플레이어 라운드 (다) — 다중 동시 스트림 TDD.

권위 출처(이것만 신뢰): design/replay/handoff_round_c.md.
**src/sepsis/replay/orchestrator.py 구현은 안 보고** 핸드오프 §3.2(시그니처)·§4(실패모드)·
§5(합격기준)만 신뢰해 작성한다(출제자-응시자 분리). 구현 전이면 RED(ImportError)가 정상.

커버하는 합격기준(§5): 3 동시 분리 · 4 중복 가드 · 5 빈 입력 · 6 무상태(구조).
"""
from __future__ import annotations

import threading

import pytest

from sepsis.replay.orchestrator import replay_many


class FakeSource:
    """patient_id + __iter__(행 yield) 만 가진 RowSource 구조(엔진 (가)의 FakeSource와 동형)."""

    def __init__(self, patient_id: str, rows: list[dict]):
        self.patient_id = patient_id
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class ThreadSafeSpy:
    """동시 호출을 락으로 받아적는 우체통(공유 sender, §3.2 '스레드 안전' 전제 충족)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.calls: list[tuple[str, dict]] = []

    def send(self, patient_id: str, features: dict) -> dict:
        with self._lock:
            self.calls.append((patient_id, dict(features)))   # 호출 시점 박제
        return {"patient_id": patient_id, "p": 0.1, "alarm": False}


def _noop_sleep(_seconds):  # 스레드 안전(상태 없음) — 테스트는 실제로 안 잔다
    pass


def test_concurrent_streams_isolated_and_ordered():
    """§5-3 동시 분리: 각 환자가 자기 행만 순서대로 받고, 결과는 입력 sources 순서로 인덱싱."""
    s0 = FakeSource("pA", [{"HR": 1.0}, {"HR": 2.0}, {"HR": 3.0}])
    s1 = FakeSource("pB", [{"HR": 10.0}, {"HR": 20.0}])
    s2 = FakeSource("pC", [{"HR": 100.0}])
    spy = ThreadSafeSpy()

    results = replay_many([s0, s1, s2], spy, speed=1e9, sleep_fn=_noop_sleep)

    # 결과 구조: 입력 순서대로, 각 환자 행 수만큼 응답
    assert [len(r) for r in results] == [3, 2, 1]

    # 환자별로 묶어 *내부 순서* 보존 단언(환자 간 인터리브 순서는 단언 안 함, §4 F-c2)
    by_pid: dict[str, list[float]] = {"pA": [], "pB": [], "pC": []}
    for pid, feats in spy.calls:
        by_pid[pid].append(feats["HR"])
    assert by_pid["pA"] == [1.0, 2.0, 3.0]
    assert by_pid["pB"] == [10.0, 20.0]
    assert by_pid["pC"] == [100.0]
    assert len(spy.calls) == 6   # 총 전송 = 행 합


def test_duplicate_patient_id_raises():
    """§5-4 / F-c1: 동시 스트림에 중복 patient_id면 서버 hidden state 충돌 → ValueError."""
    a = FakeSource("dup", [{"HR": 1.0}])
    b = FakeSource("dup", [{"HR": 2.0}])
    with pytest.raises(ValueError):
        replay_many([a, b], ThreadSafeSpy(), speed=1e9, sleep_fn=_noop_sleep)


def test_empty_sources_returns_empty():
    """§5-5: 빈 입력 → 빈 리스트(센더 호출 0)."""
    spy = ThreadSafeSpy()
    assert replay_many([], spy, speed=1e9, sleep_fn=_noop_sleep) == []
    assert spy.calls == []


def test_single_source_matches_engine_contract():
    """§5-6 정직성: 한 source면 (가) 엔진과 동일 — 행 그대로·순서대로 1회씩."""
    src = FakeSource("solo", [{"HR": 7.0, "O2Sat": None}, {"HR": 8.0, "O2Sat": 95.0}])
    spy = ThreadSafeSpy()
    results = replay_many([src], spy, speed=1e9, sleep_fn=_noop_sleep)
    assert len(results) == 1 and len(results[0]) == 2
    assert spy.calls == [("solo", {"HR": 7.0, "O2Sat": None}),
                         ("solo", {"HR": 8.0, "O2Sat": 95.0})]   # None 그대로(채움 없음)
