"""Locust 부하테스트 드라이버 (나) — 측정 드라이버(프로덕션 코드 불변).

설계: docs/design/load-test/. 최상위 패키지명은 `loadtest`(설계 디렉토리명과 일치).
- patient_pool: 미사용 환자 배타 배정(반복 금지·스레드세이프).
- request_builder: PSV 행 → /predict 페이로드(결측 None 보존).
- locustfile: HttpUser 시나리오(스모크로 실행 — `locust -f loadtest/locustfile.py`).
- run_matrix: N×코어 sweep 오케스트레이션(사람이 up 후 실행).

주: 최상위명을 `bench`로 두면 테스트 패키지 `tests/bench`(sys.path 우선)와 충돌해
`import bench.load`가 그쪽으로 해석된다 → `loadtest`로 명명(핸드오프 §1의 bench/load 의도 유지).
"""
