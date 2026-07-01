"""리플레이어 — 녹화된 환자 시계열을 시간 간격대로 서빙 /predict 에 흘려보낸다.

라운드 (가): 스트림 엔진(engine) + .psv 어댑터(psv_source) + httpx sender(http_sender).
권위 설계: docs/design/replay/handoff_round_a.md.
"""
