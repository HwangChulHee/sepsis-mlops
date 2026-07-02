"""PatientPool — setB PSV 풀에서 미사용 환자를 배타적으로 하나씩 내준다.

설계 근거(docs/design/load-test/): 결정 2·핸드오프 §2.1.
- **배타 배정(M1)**: 각 Locust User는 실행 내내 배타적 distinct 환자를 점유한다.
  두 User가 같은 pid를 밀면 서버 per-pid 락이 직렬화는 하나 두 스트림 timestep이
  뒤섞여 causal 붕괴한다 → 한 파일은 정확히 한 번만 claim된다.
- **반복 금지(B1)**: 같은 pid를 다시 틀면 서버가 이전 hidden state를 이어받아 곡선이
  오염된다(psv_source.py F4). 서버엔 /reset 엔드포인트가 없으므로(프로덕션 미수정=범위 밖)
  반복하지 않고 미사용 환자로 교체한다.
- **고갈(B1-r2)**: 풀 소진 시 claim()은 None(예외 아님) → User 정지. 각 부하 칸은
  풀 고갈 전 유한 지속시간으로 돌린다. run_suffix 무한 pid 재활용은 택하지 않는다.
- **스레드세이프**: Locust User는 여러 그린렛/스레드 — 원자 배정으로 이중 claim 차단.
"""
from __future__ import annotations

import random
import threading
from pathlib import Path


class PatientPool:
    """PSV 파일 풀의 배타·비반복·스레드세이프 배정기.

    claim() 은 아직 아무도 안 쓴 파일 하나를 원자적으로 내주고, 소진 시 None 을 준다.
    """

    def __init__(
        self,
        source_dir,
        pattern: str = "*.psv",
        shuffle: bool = False,
        seed: int | None = None,
    ):
        self._dir = Path(source_dir)
        files = sorted(self._dir.glob(pattern))   # 결정론적 기본 순서(파일명)
        if shuffle:
            random.Random(seed).shuffle(files)     # 대표성용 셔플(재현 위해 seed)
        self._files: list[Path] = files
        self._idx = 0                              # 다음에 내줄 인덱스(단조 증가 = 비반복)
        self._lock = threading.Lock()
        self._total = len(self._files)

    @property
    def total(self) -> int:
        """풀의 전체 환자 수(불변)."""
        return self._total

    @property
    def remaining(self) -> int:
        """아직 claim 안 된 환자 수."""
        with self._lock:
            return self._total - self._idx

    def claim(self) -> Path | None:
        """미사용 환자 파일 하나를 배타적으로 반환. 소진 시 None.

        인덱스를 락 아래 단조 증가시켜, 한 파일이 두 번 배정되지 않게 한다(배타·비반복).
        """
        with self._lock:
            if self._idx >= self._total:
                return None
            path = self._files[self._idx]
            self._idx += 1
            return path
