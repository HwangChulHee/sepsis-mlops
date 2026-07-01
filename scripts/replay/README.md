# scripts/replay — 궤적 재생

실환자 .psv 궤적을 서빙 `/predict`에 "재생 버튼"처럼 흘려 실모델 위험도를 관측(파이프라인 독립).

| 스크립트 | 한 일 |
|---|---|
| `replay_patient.py` | 환자 1명 재생 |
| `replay_ward.py` | 여러 환자 **동시** 재생(다중 스트림) |

실행: `uv run python -m scripts.replay.replay_patient --help`
