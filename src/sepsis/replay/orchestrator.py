"""다중 동시 스트림 — 무상태 엔진 위에 N개 source 를 동시 구동 (핸드오프 라운드 다 §3.2).

(가) 엔진(replay_stream)은 무상태(F5)라 source 당 스레드 1개로 그대로 N개 얹으면 된다.
환자 분리는 **서버 hidden state(patient_id) + source 별 독립 커서**가 보장한다 — 엔진은
여전히 환자 0보유. sender 는 스레드 간 *공유*되므로 스레드 안전이어야 한다(HttpSender 의
httpx.Client 는 스레드 안전).

실패모드 거울(§4):
- F-c1 환자 섞임: 같은 patient_id 둘 이상 동시 재생 → 서버 hidden state 충돌. 시작 전 ValueError.
- F-c2 순서: 각 환자 내부 행 순서는 엔진(F2)이 보존. 스레드는 환자 *간* 인터리브만 만든다.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from sepsis.replay.engine import RowSource, Sender, replay_stream


def replay_many(
    sources: list[RowSource],
    sender: Sender,
    *,
    speed: float,
    sleep_fn: Callable[[float], None] = time.sleep,
    max_workers: int | None = None,
) -> list[list[dict]]:
    """sources 를 source 당 스레드 1개로 동시 재생한다(각자 replay_stream).

    Args:
        sources:  RowSource 리스트. 각 patient_id 는 서로 **유일**해야 한다(F-c1).
        sender:   공유 Sender — **스레드 안전 필수**(HttpSender 는 안전).
        speed:    시간 압축비(엔진과 동일 의미). >0, 아니면 엔진이 ValueError.
        sleep_fn: 주입 가능한 sleep(테스트는 no-op). 스레드에서 동시 호출되므로 안전해야 함.
        max_workers: 동시 스레드 수. 기본 = source 수(전부 동시).

    Returns:
        입력 sources 와 **같은 인덱스**의 응답 리스트들의 리스트. 빈 입력 → [].
    """
    sources = list(sources)
    if not sources:
        return []

    # F-c1: 중복 patient_id 는 서버 hidden state 를 섞으므로 시작 전에 막는다.
    pids = [s.patient_id for s in sources]
    dups = sorted({p for p in pids if pids.count(p) > 1})
    if dups:
        raise ValueError(
            f"동시 스트림에 중복 patient_id {dups} — 서버 hidden state 가 섞여 곡선이 오염된다. "
            f"PsvRowSource(run_suffix=...) 로 환자 id 를 유일화하라."
        )

    workers = max_workers if max_workers is not None else len(sources)
    results: list[list[dict] | None] = [None] * len(sources)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(replay_stream, s, sender, speed=speed, sleep_fn=sleep_fn): i
            for i, s in enumerate(sources)
        }
        for fut, i in futures.items():
            results[i] = fut.result()   # 스레드 예외는 그대로 전파(fail loud)
    return results  # type: ignore[return-value]  # 위 루프가 전 인덱스를 채움
