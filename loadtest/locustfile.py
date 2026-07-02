"""Locust 시나리오 — 가상 User = 환자 1명, PSV 순서 재생 (부하테스트 (나)).

설계: docs/design/load-test/ 결정 2·3 / 핸드오프 §2.3.

실행(사람이 (가) 스택 up 후):
    uv add --dev locust          # 최초 1회
    locust -f loadtest/locustfile.py --host http://localhost:8000 \
           --headless -u <N> -r <ramp> --run-time <T>

불변식:
- 각 User는 미사용 환자를 배타 점유(PatientPool.claim) → 두 User가 같은 pid 안 밈(causal).
- 한 환자 PSV를 파일=시간 순서로 /predict 에 전송(재배치 금지).
- 스트림 끝나면 반복하지 않고 다음 미사용 환자로 교체. 풀 소진 시 User 정지(유한).
- 타깃은 serving 직접 :8000 (front-nginx 우회 — 순수 서빙 latency).
"""
from __future__ import annotations

import os

from locust import HttpUser, task, between

from sepsis.replay.psv_source import PsvRowSource  # 재사용 로더(PSV→{col:None|float} 시간순)
from sepsis import config as C

from loadtest.patient_pool import PatientPool
from loadtest.request_builder import build_predict_payload

# 환자 풀은 프로세스 전역 1개(모든 User가 공유, 배타 배정). 소스는 setB 기본.
_POOL_DIR = os.environ.get("LOADTEST_PATIENT_DIR", str(C.DATA_DIR / "training_setB"))
_FEATURESET = os.environ.get("SERVE_FEATURESET", "vitals")
_POOL = PatientPool(_POOL_DIR, shuffle=True, seed=int(os.environ.get("LOADTEST_SEED", "0")))


class SepsisPatientUser(HttpUser):
    """한 환자의 PSV 스트림을 순서대로 /predict 에 미는 가상 사용자."""

    wait_time = between(0.0, 0.0)   # 지속 스트림(버스트 아님) — timestep 사이 대기 0

    def on_start(self):
        self._rows: list[dict] = []
        self._i = 0
        self._patient_id: str | None = None
        self._load_next_patient()

    def _load_next_patient(self) -> bool:
        """미사용 환자 하나를 배타 확보해 행 스트림을 로드. 없으면 User 정지."""
        path = _POOL.claim()
        if path is None:
            # 풀 고갈 — 더 줄 미사용 환자 없음(반복 재사용 금지). 이 User 정지.
            self.environment.runner.quit() if self.environment.runner else None
            self.stop()
            return False
        src = PsvRowSource(path, featureset=_FEATURESET)   # 파일=시간순, {col:None|float}
        self._rows = list(src)
        self._i = 0
        self._patient_id = src.patient_id
        return True

    @task
    def send_next_timestep(self):
        if self._patient_id is None:
            return
        if self._i >= len(self._rows):
            # 스트림 끝 → 반복하지 않고 다음 미사용 환자로 교체(결정 2).
            if not self._load_next_patient():
                return
        row = self._rows[self._i]
        self._i += 1
        payload = build_predict_payload(row, patient_id=self._patient_id)
        # name= 로 모든 timestep 을 한 엔드포인트로 집계(환자별 URL 분산 방지).
        self.client.post("/predict", json=payload, name="/predict")
