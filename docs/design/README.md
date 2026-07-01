# docs/design/ — 설계 결정 인덱스

풀 MLOps 파이프라인의 **설계 결정·검토·핸드오프**를 스테이지별 폴더로 묶었다.
규칙은 [`WORKFLOW.md`](WORKFLOW.md) (3단계: 설계결정 → 레드팀 검토(게이트) → 구현 핸드오프).

각 스테이지 폴더 = `decisions.md`(DDD·단일 진실원) · `review.md`(DDD 게이트) · `handoff.md`(자립 실행 명세) · `handoff_review.md`(실행 명세 게이트).

## 스테이지 맵

| 스테이지 | 폴더 | 상태 | 핵심 결정 / 산출 |
|---|---|---|---|
| **H1** 전처리·피처·분할 | [`h1/`](h1/) | ✅ 구현 완료 | 환자단위 A→B 분할, train-only 정규화, 마스크 OFF(옵트인), m2m 시퀀스 |
| **H2** 학습 (baseline+GRU+ablation) | [`h2/`](h2/) | ✅ 구현 완료 | 대표 baseline=**XGBoost**, 메인 피처셋=**미결**(H3·H4 후), 6조합 A-val |
| **H3** cross-site 평가 (B 개봉) | [`h3/`](h3/) | ✅ 구현 완료 | A→B gap, 공식 utility 동등성, **마스크 OFF**(cross-site 전이성으로 확정) |
| **H4-서빙** 실시간 서빙 | [`h4/serving/`](h4/serving/) | ✅ 구현 완료 | FastAPI causal GRU, A 동결 전처리(skew 0), 원자 번들, replicas=1 |
| **H4-드리프트** covariate 감시 | [`h4/drift/`](h4/drift/) | ✅ 구현 완료 | 거리지표(Evidently, KS 폴백 차단), 환자당 1관측, 합성주입 보정, watch 전용 |
| **H4-재학습** watch→재학습 | [`h4/retrain/`](h4/retrain/) | ✅ 구현 완료 | 드리프트 주도·성능 보조, human-in-the-loop, B=운영데이터, 버전드 안전 교체·롤백 |

> **MLOps 루프 폐쇄**: 서빙 → 드리프트 감시 → 재학습 → 새 번들(버전드 교체) → 서빙.

## 관련 디렉토리

- [`../research/`](../research/) — 근거 탐구 Q1~Q5 (왜 이 방법이 맞나). `design`의 재료.
- [`../reports/`](../reports/) — 측정·검증 결과: `eda_findings.md` · `h1_diagnostics.md` · `h2_results.md` · `h3_results.md` · `smoke_findings.md`.
- `../src/sepsis/` — 핸드오프로 생성된 풀 학습·서빙·드리프트·재학습 코드.

## 읽는 순서 (한 스테이지 따라가기)

`decisions.md` (무엇을 왜) → `review.md` (게이트: 구멍/HOLD) → `handoff.md` (어떻게 실행) → `handoff_review.md` (실행 게이트) → 결과는 `../reports/` · `../src/sepsis/`.
