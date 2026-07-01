# scripts/h2 — 학습 (H2)

트리(XGBoost·LightGBM) vs GRU m2m, 6조합 + 공식 utility로 대표 baseline 선택.

| 스크립트 | 한 일 |
|---|---|
| `h2a_utility_check.py` | 공식 utility 구현 + 검증 게이트 |
| `h2b_train_trees.py` | 트리 학습 + 강건성 |
| `h2c_train_gru.py` | GRU m2m 풀 학습 |
| `h2d_select.py` | 6조합 집계 → 대표 baseline 선택 |
| `smoke_m2m.py` | H1 m2m 재-스모크(사람 체크포인트) |

실행: `uv run python -m scripts.h2.h2b_train_trees`
