# H2 결과 — 6조합 A-val 집계 · 대표 baseline 선정

> 생성: H2-d (`scripts/h2/h2d_select.py`) · 2026-06-28 · 입력 H2-b 0560de7 · H2-c 2252d6f
> **A-val 전용** 집계(cross_site의 학습 split). **B는 봉인** — H3에서만 펼침.
> 지표: PR-AUC(GRU는 masked) · 공식 utility(τ는 A-val utility 최대화로 선정) · 정확도 미사용.

## 6조합 A-val 결과 (utility 내림차순)

| 순위 | 모델 | featureset | PR-AUC | utility | τ |
|---:|---|---|---:|---:|---:|
| 1 | gru | vitals | 0.1140 | **0.4087** | 0.5732 |
| 2 | gru | vitals_labs | 0.1061 | **0.3935** | 0.5558 |
| 3 | xgboost | vitals_labs | 0.0695 | **0.2685** | 0.4926 |
| 4 | xgboost | vitals | 0.0673 | **0.2200** | 0.5468 |
| 5 | lightgbm | vitals_labs | 0.0526 | **0.2062** | 0.5220 |
| 6 | lightgbm | vitals | 0.0466 | **0.1845** | 0.6410 |

- 랜덤 기준선 PR-AUC ≈ 0.018(양성 비율) — 전 조합이 유의 상회(배선이 실제 학습).
- GRU PR-AUC는 **masked**(패딩 제외). unmasked는 패딩 음성이 섞여 더 낮음(예: GRU vitals masked 0.114 vs unmasked 0.050) — MLflow `a_val_prauc_unmasked` 참조.

## Robustness — featureset 비교의 HP 동결 편향 (H2-b)

vitals_labs에서 찾은 HP\*를 vitals에 동결 적용한 게 vitals를 불리하게 하는가?

| 모델 | vitals 자체최적 util | 동결HP-vitals util | Δ(self−frozen) |
|---|---:|---:|---:|
| xgboost | 0.2214 | 0.2200 | +0.0014 |
| lightgbm | 0.1845 | 0.1845 | +0.0000 |

- Δ≈0 → **동결 HP 편향 무시 가능**, featureset 비교 공정. (GRU도 vitals_labs HP\*가 vitals에서 오히려 더 좋음 → 동일 결론.)

## 대표 baseline 선정 (결정 7)

- **확정: `XGBOOST`** (사람 결정 — A-val utility 기준, **B 미사용**).
- 자동 집계도 동일: xgboost A-val utility 0.2685 > lightgbm 0.2062 (utility-primary; B sealed). Dominates on BOTH featuresets.
- 두 부스터 featureset별 A-val utility:
  - xgboost: vitals=0.2200, vitals_labs=0.2685
  - lightgbm: vitals=0.1845, vitals_labs=0.2062
- 부록(두 부스터 차이): XGBoost가 두 featureset 모두에서 LightGBM 상회 — 결측 내장 처리·요약통계 입력에서 XGBoost가 더 강건. LightGBM은 우승 계열 정렬용으로 보존(H1 결정 6), GRU와의 비교 기준선은 XGBoost.

## 메인 featureset — ⏸ 미결 (확정하지 않음)

**vitals(9) vs vitals_labs(18)를 H2에서 확정하지 않는다.** 사유:
1. **모델별 방향이 갈림** — 트리는 vitals_labs 우세(검사 도움), **GRU는 vitals 우세**(util 0.4087 > 0.3935). A-val만으로 단일 메인을 단정하기 이르다.
2. **A-val은 in-site** — 진짜 판단 기준은 **H3 cross-site(A→B) + H4 드리프트**. 운영환경 일반화를 본 뒤 결정한다.
3. **재설정 가능 설계** — 파이프라인이 featureset를 설정값(`config.FEATURESETS`)으로 받아 H3/H4에서 재선택 가능. 지금 고정할 필요가 없다.
→ **결정 이연: H3 cross-site B + H4 드리프트 평가 후.**

## 핵심 인상

- **GRU > 트리 (A-val).** GRU util ~0.39–0.41 vs 트리 최고 0.27 — "시간 흐름을 통째로 배우는 GRU가 요약통계 트리를 이긴다"는 H2 핵심 가설이 A-val에서 선명.
- **featureset 방향이 모델별로 갈림** (위 미결 사유 1).
- **robustness Δ≈0** — featureset 비교는 공정.
- ⚠️ **단, 전부 in-site(A-val).** GRU util 0.4는 우승팀 0.36(cross-site)보다 높지만 도메인 시프트가 없어 낙관 편향 가능. **본게임은 H3 cross-site(A→B).**

## H2 종료

- 6조합(모델3 × 피처셋2) 학습·A-val 평가 **완료**. 게이트: H2-a 5/5 · H2-b 7/7 · H2-c 6/6.
- **대표 baseline = XGBOOST** 확정. **메인 featureset = 미결**(H3·H4 후).
- 누수 가드: B 봉인(동적 B-guard) · τ featureset별 개별 · scale_pos_weight 고정 · train-only 통계. 아티팩트(native 모델 + 전처리통계 + τ) MLflow 저장 → H3 B 재현 준비 완료.
- 다음: **H3** (B 펼쳐 cross-site 채점 · utility 정밀검증 · 마스크 누수검증).
