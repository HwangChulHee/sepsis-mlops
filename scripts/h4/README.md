# scripts/h4 — 운영 (H4)

서빙·드리프트·재학습으로 MLOps 루프를 닫는다. `*_smoke.py`는 프로그램적 게이트.

| 스크립트 | 한 일 |
|---|---|
| `h4s_smoke.py` · `h4s_b_smoke.py` · `h4s_c_smoke.py` | 서빙 코어 → FastAPI/Prometheus → Docker/K8s |
| `h4d_a_smoke.py` · `h4d_b_smoke.py` · `h4_drift_loop_smoke.py` | 드리프트 엔진 → 감시 → 서빙 연동 루프 |
| `h4r_a_smoke.py` · `h4r_b_smoke.py` · `h4r_c_smoke.py` | watch→action → 재학습 → 안전 버전 교체·롤백 |
| `h4s_export_bundle.py` | MLflow run → 이식 가능한 버전드 번들 export |
| `gen_synth_bundle.py` | minikube 전파용 합성 번들 생성기 |

실행: `uv run python -m scripts.h4.h4s_smoke`
