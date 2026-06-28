# H3-b 결과 — cross-site (A→B) 채점

> 생성: H3-b (`scripts/h3b_crosssite.py`) · 2026-06-28 · setB **1회 개봉**(채점 전용).
> **A 동결 아티팩트만**(μ/σ·fill·clip·τ) 사용 — B 재계산·재튜닝·τ재선정 없음.
> A-val 점수는 `reports/h2_results.md` 인용(재계산 아님). gap = A_val − B.
> ⚠️ **B는 관찰 전용** — 피처셋/모델 선택은 A-val+H4, B 점수로 고르지 않음.

## 6조합 utility · PR-AUC (A-val rank 순)

| 모델 | featureset | A_util | B_util | gap(util) | A_PR | B_PR | gap(PR) | τ |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| gru | vitals | 0.4087 | 0.2466 | +0.1621 | 0.1140 | 0.0782 | +0.0358 | 0.5732 |
| gru | vitals_labs | 0.3935 | 0.1285 | +0.2650 | 0.1061 | 0.0703 | +0.0358 | 0.5558 |
| xgboost | vitals_labs | 0.2685 | 0.0546 | +0.2139 | 0.0695 | 0.0346 | +0.0349 | 0.4926 |
| xgboost | vitals | 0.2200 | 0.0234 | +0.1966 | 0.0673 | 0.0363 | +0.0310 | 0.5468 |
| lightgbm | vitals_labs | 0.2062 | -0.0379 | +0.2441 | 0.0526 | 0.0247 | +0.0279 | 0.5220 |
| lightgbm | vitals | 0.1845 | 0.0184 | +0.1661 | 0.0466 | 0.0268 | +0.0198 | 0.6410 |

- **A-val 순위**: gru/vitals > gru/vitals_labs > xgboost/vitals_labs > xgboost/vitals > lightgbm/vitals_labs > lightgbm/vitals
- **B 순위**: gru/vitals > gru/vitals_labs > xgboost/vitals_labs > xgboost/vitals > lightgbm/vitals > lightgbm/vitals_labs
- **순위 역전**: 있음
- gap>0 = B에서 성능 하락(cross-site degradation). 해석은 사람 체크포인트.

## 누수 가드
- frozen-only 채점(`eval/crosssite.py`): fit/tune/select 미호출(grep), GRU μ/σ·fill·clip이 아티팩트와 bit-동일, τ는 A-val 동결값.
- B는 채점에만. 피처셋/모델 선택에 B 미사용(관찰 전용).

## 다음
- ⏸ 사람: gap·순위역전 해석(GRU vs 트리 일반화, 피처셋 방향) — **B로 선택하지 않음**.
- H3-c: 마스크 ON/OFF의 A→B gap 비교(전이성) → 마스크 OFF 최종 확정.
