"""스트림 엔진 — source 의 행을 시간순으로 sender 에 흘린다 (핸드오프 §3.1).

핵심 불변식(핸드오프 §4 실패모드의 거울):
- F1 0-fill 금지: 엔진은 행을 **그대로** 전달한다. 채움/clip/정규화 일절 없음(전처리는 서버 몫, §2).
- F2 causal: source 순서 그대로 0→1→…→T-1. 정렬/재배치/건너뜀/중복 없음.
- F5 무상태: 엔진은 환자별 상태를 0 보유한다. patient_id 는 매 호출 source 에서 읽어
  send 에 그대로 넘긴다 — 전역/캐시/인스턴스에 환자를 박지 않는다(다음 라운드 다중 스트림의 전제).
- F6 가드: speed<=0 은 ValueError(무한/음수 대기 차단).

sleep 의미(§3.1): 행 0 은 즉시 전송, 행 1..T-1 은 각각 전송 **전에** sleep_fn(3600/speed).
→ T 개 전송, T-1 번 sleep, 마지막 뒤 trailing sleep 없음.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Protocol


class RowSource(Protocol):
    """시간순 행을 내주는 소스. patient_id 는 스트림 내내 고정(같은 환자 = 같은 id)."""

    patient_id: str

    def __iter__(self) -> Iterator[dict[str, float | None]]:
        ...
    # 각 yield = 한 타임스텝. 키 = featureset 컬럼. 결측 = None(어댑터가 NaN→None 변환).


class Sender(Protocol):
    """행 하나를 서버(또는 가짜)에 보내고 응답 dict 를 돌려준다."""

    def send(self, patient_id: str, features: dict[str, float | None]) -> dict:
        ...


def replay_stream(
    source: RowSource,
    sender: Sender,
    *,
    speed: float,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> list[dict]:
    """source 의 행을 시간순으로 sender 에 흘린다. 행 사이 간격 = 3600/speed 초.

    Args:
        source: patient_id 속성 + __iter__(행 dict yield) 를 가진 RowSource.
        sender: send(patient_id, features)->dict 를 가진 Sender.
        speed:  시간 압축비. 3600 → 1시간을 1초로. 1 → 실시간. speed<=0 은 ValueError.
        sleep_fn: 주입 가능한 sleep(테스트는 가짜로 인자 기록). 기본 time.sleep.

    Returns:
        send 응답 dict 들의 리스트(검사·데모용, 순서 보존).
    """
    if speed <= 0:
        raise ValueError(f"speed must be > 0, got {speed!r}")

    interval = 3600.0 / speed
    pid = source.patient_id          # F5: 매 send 에 그대로 넘길 뿐, 엔진이 들고 있지 않음
    responses: list[dict] = []

    for i, row in enumerate(source):
        if i > 0:                    # 행 0 은 즉시; 1..T-1 은 전송 전 sleep (T-1 번)
            sleep_fn(interval)
        # 행을 그대로 전달 — 채움/clip/정규화 없음(F1), 키 주입 없음(F3은 source/어댑터가 보장)
        responses.append(sender.send(pid, row))

    return responses
