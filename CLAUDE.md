# CLAUDE.md — sepsis-mlops

ICU 환자 시계열로 패혈증을 조기 예측하는 MLOps 프로젝트. PhysioNet/CinC 2019 데이터(공개 set A+B, 40,336명). 도메인 이전 MLOps 시리즈의 3번째(산업센서 → 의료영상 → **의료시계열**).

> 이 파일은 매 세션 자동으로 읽힌다. 상세 규칙은 [`docs/design/WORKFLOW.md`](docs/design/WORKFLOW.md) 참고.

---

## 개발 워크플로우 (반드시 따를 것)

설계는 3단계로 진행한다: **[1] 설계결정문서(DDD) → [2] 레드팀 검토(게이트) → [3] 구현 핸드오프.**

검토·보완·테스트는 `.claude/`의 에이전트 시스템으로 돌린다:

**에이전트** (독립 context — `.claude/agents/`)
- `redteam` — 적대적 검토. 동의가 아니라 반박. read-only. blocker/major/minor 판정.
- `reviser` — redteam의 blocker를 실제로 보완하고 라운드 단위로 커밋. 표면 덮기 금지.
- `spec-writer` — 설계만 보고 TDD 테스트 작성. **구현 코드는 보지 않는다**(출제자-응시자 분리).

**스킬** (메인 절차 — `.claude/skills/`)
- `review-loop` — redteam ⇄ reviser 루프 지휘. **통과 = blocker 0**, 최대 3라운드, 초과 시 **사람 에스컬레이션**. 루프 중 로컬 커밋만, 통과하면 자동 푸시(에스컬레이션은 푸시 안 함).
- `mentor` — CTO·아키텍트 관점으로 현재 상태·트레이드오프·기술 부채를 정직하게(치어리더 금지) 쉽게 설명.

> 설계/핸드오프 검토를 돌릴 땐 `review-loop`를 쓴다. 두 문서를 섞지 않는다 — *DDD = 설계 정당화*, *핸드오프 = 기계적 실행*.

## 출처 등급 (모든 근거에 표기)
`[확인됨]`(1차 출처 직접 확인) · `[유도]`(공식값에서 계산) · `[우리 결정]`(우리 판단) · `[검증 필요]`(재확인 예정)

## 누수 방지 대원칙 (전 단계 공통 — 설계·구현·테스트 모두 적용)
- **환자 단위 분할** — 같은 환자가 train/test에 걸치면 안 됨(파일 1개 = 환자 1명).
- **train-only 정규화** — 평균/표준편차는 학습 split에서만 계산.
- **0으로 채우지 않음** — 의료에서 0은 진짜 값(예: 혈압 0 = 사망). 결측은 ffill→train 평균.
- **ICULOS 제외** — 우측 절단 구조를 컨닝하는 시간 단서.
- **결측 마스크는 기본 OFF(옵트인)** — 치료행동 누수 통로(근거: `docs/research/04_*`). 검증 통과 시에만.
- **결측 처리는 모델별 분기** — XGBoost는 NaN 그대로, GRU는 ffill + train 평균.

## 평가 기조
정확도 버림(불균형 1.8% 양성). **PR-AUC 1차 + 공식 utility score + cross-site(A→B)** 로 일반화 직접 측정.

---

## 디렉토리
- `docs/research/` — 근거 탐구 Q1~Q5 (왜 이 방법이 맞나)
- `docs/design/` — 실행 결정 (DDD·검토·핸드오프) ← 개발은 여기서
- `docs/reports/` — EDA·스모크 측정 결과
- `smoke/` — 검증된 시제품 파이프라인 (재사용)
- `src/sepsis/` — 풀 학습 코드 (핸드오프로 생성)
- `docs/adr/` — 아키텍처 결정 기록 (서빙·확장 등)
- `.claude/` — 에이전트(`agents/`)·스킬(`skills/`) 정의

## 단계
풀 학습 = **H1**(전처리·피처·분할) → **H2**(학습+ablation) → **H3**(평가·누수검증). 운영 = **H4**(서빙·드리프트·재학습) — 구현 완료, MLOps 루프 폐쇄. 운영 콘솔 = **console**(R1~R3 배포 승인·버전·감사) — 진행 중.

온프렘 배포 = **온프렘 Compose 통합 스택(가)** — 구현·SM 실측 완료(`docs/design/onprem-compose/`, 리포트 `docs/reports/onprem_compose_smoke.md`). 부하테스트 = **Locust (나)** — 드라이버·실측 완료, 서버 천장 ~700rps·병원 700× 여유·SM-3 종결(`docs/design/load-test/`, 리포트 `docs/reports/load_test_results.md`).