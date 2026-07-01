# H3-b 결과 — cross-site (A→B) 채점

> 생성: H3-b (`scripts/h3/h3b_crosssite.py`) · 2026-06-28 · setB **1회 개봉**(채점 전용).
> **A 동결 아티팩트만**(μ/σ·fill·clip·τ) 사용 — B 재계산·재튜닝·τ재선정 없음.
> A-val 점수는 `docs/reports/h2_results.md` 인용(재계산 아님). gap = A_val − B.
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

---

## H3-c — 마스크 누수 검증 (전이성, GRU vitals)

> 마스크 OFF(H2) vs ON(재학습, input_dim F→2F). 판정 = A-val→B **gap 비교**.
> 마스크 채널: RAW NaN·ffill 이전 생성 → 정규화 피처와 concat(마스크는 z-score 제외).
> ON 학습은 A만, B는 frozen-only 채점(누수 없음). B는 관찰 전용.

| 변형 | A_util | B_util | gap(A−B) | B_PR | τ |
|---|---:|---:|---:|---:|---:|
| 마스크 OFF | 0.4087 | 0.2466 | +0.1621 | 0.0782 | 0.5732 |
| 마스크 ON | 0.4473 | 0.2127 | +0.2346 | 0.0749 | 0.5382 |

- **Δgap (ON−OFF) = +0.0725**. gap↑(양수) → 마스크가 site-specific 측정패턴을 학습해 cross-site 전이가 더 나빠짐 → **OFF 정당**. Δgap≈0/음수 → 마스크가 전이를 해치지 않음.
- 마스크 채널 무결성: 채널 평균 [0.921999990940094, 0.8799999952316284, 0.33799999952316284, 0.8479999899864197, 0.8970000147819519, 0.5189999938011169, 0.9020000100135803, 1.0, 1.0] ≈ 관측률 [0.922, 0.88, 0.338, 0.848, 0.897, 0.519, 0.902, 1.0, 1.0] (all-ones 아님 — ffill 이전 생성 확인).
- ⏸ **사람 체크포인트**: 위 Δgap으로 **마스크 OFF 최종 확정** 판단(WORKFLOW §8 귀결). B는 관찰 전용 — 어떤 선택도 B로 하지 않음.

