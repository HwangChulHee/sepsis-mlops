"""회귀 — DriftWindow 스레드 안전성(D#1).

FastAPI 는 sync 경로 핸들러를 스레드풀에서 돌린다: /predict(add)와 /drift(patient_summary·
n_patients)가 병렬 실행되면, 락 없는 deque 는 순회 중 append 로 `RuntimeError: deque mutated
during iteration` 을 던진다. 이 테스트는 add 폭주와 동시에 읽기(patient_summary/n_patients)를
반복해, 락이 없으면(구버전) 높은 확률로 터지고 락이 있으면 무사통과함을 고정한다.
"""
from __future__ import annotations

import threading

import numpy as np

from sepsis.drift.window import DriftWindow


def test_concurrent_add_and_read_never_raises():
    w = DriftWindow(maxlen=2000)
    F = 6
    errors: list[BaseException] = []
    stop = threading.Event()

    def writer():
        i = 0
        try:
            while not stop.is_set():
                w.add(f"p{i % 500}", np.full(F, float(i), dtype=np.float32))
                i += 1
        except BaseException as e:   # noqa: BLE001 — 어떤 예외든 잡아 테스트 실패로
            errors.append(e)

    def reader(fn):
        def _run():
            try:
                while not stop.is_set():
                    fn()
            except BaseException as e:   # noqa: BLE001
                errors.append(e)
        return _run

    threads = [
        threading.Thread(target=writer),
        threading.Thread(target=writer),
        threading.Thread(target=reader(w.patient_summary)),
        threading.Thread(target=reader(w.n_patients)),
        threading.Thread(target=reader(lambda: len(w))),
    ]
    for t in threads:
        t.start()
    # 충분한 경합 시간(구버전이면 이 안에서 거의 확실히 RuntimeError).
    stop.wait(1.5)
    stop.set()
    for t in threads:
        t.join(timeout=5)

    assert not errors, f"동시 add/read 중 예외 발생(스레드 비안전): {errors[:3]}"
    # 최종 상태도 정합해야 한다.
    summ = w.patient_summary()
    assert summ.ndim == 2 and summ.shape[1] == F
    assert w.n_patients() == summ.shape[0]
