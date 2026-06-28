# H4-재학습 구현 핸드오프 — watch→action + human-in-the-loop 재학습 (루프 폐쇄)

> **설계 근거**: [`h4_retrain_decisions.md`](h4_retrain_decisions.md)(v2, 검토 PASS `1b0a1c8`). 실행 명세로 번역.
> **워크플로우**: [`WORKFLOW.md`](WORKFLOW.md). 자립형. **H4 마지막 — MLOps 루프(서빙→드리프트→재학습→새 번들→서빙) 폐쇄.**
> **개정 이력**
> - v1 (2026-06-28) — 초안. 검토 권고 흡수: **post-retrain cross-site 측정 불가 명시**(A+B 학습 시 미관측 제3 분포 없음 → B-holdout=in-distribution 검증, cross-site 주장 아님), human-in-loop 자동경로 0건 grep, 시뮬 범위 명문화, 검증 임계 수치화. DDD cosmetic(결정 4 중복 헤더 1줄 삭제).

---

## 0. 공통 규칙 (자립형)

### 환경 / 입력
- 기존 환경. CPU(재학습은 배포 조합만이라 가벼움). 외부 레포 참조 금지.
- 입력: drift watch 신호(`drift/watch.py`의 DRIFT_STATE·DATASET_DRIFT_SHARE), H1~H2 학습 파이프라인(`train/`), setB(H1 캐시), export 패턴(`scripts/h4s_export_bundle.py` → 버전드 확장), 서빙 번들·ConfigMap RUN.

### ★ 핵심 원칙 (DDD)
- **드리프트 주도·성능 보조**: 드리프트 = 성능 하락 *위험* 신호(≠ 확정). action = "조사 권고", 자동 재학습 아님.
- **human-in-the-loop**: 재학습 트리거·교체 승인은 사람. **자동 재학습·자동 교체 경로 0건**(grep 강제).
- **B = 운영 데이터 가정**: 신규 데이터 없음(A/B 고정) → B를 운영 데이터로 봄. **B-retrain/B-holdout 환자 단위 분할**, A+B-retrain 재학습, B-holdout 검증.
- **★ cross-site 주장 금지**: 재학습 모델은 A+B 다 봤으므로 **미관측 제3 분포 없음**. B-holdout 검증 = **in-distribution**(새 데이터 성능 + A 무회귀)이지 cross-site 일반화 아님. 진짜 cross-site는 제3 병원(C) 필요(우리 셋엔 없음) — 한계 명시.
- **우산장수**: 인지·명시, 태깅 미구현(개입 컬럼 없음). 성능을 주 트리거로 안 써서 영향 최소화.
- **시뮬레이션**: 실제 라벨·개입 스트림 없음 → 지연 라벨·트리거를 시뮬레이션해 **구조 검증**.

### 진행 규칙
- 각 토막 commit & push. PASS 프로그래매틱, 실패 시 정지·보고.
- **H4r-a PASS → 자동 b → c** (이상 없으면). a 실패 시 정지.
- **교체(c)는 사람 승인 시뮬레이션 체크포인트** 포함(자동 아님).

### 디렉토리 (생성)
```
src/sepsis/retrain/
  promote.py       # H4r-a: watch→action 승격(조사 권고, 자동재학습 0건)
  backfill.py      # H4r-a: 지연 라벨 백필(시뮬), 성능 보조
  pipeline.py      # H4r-b: 재학습(B 운영데이터, retrain/holdout 분할)
  validate.py      # H4r-b: B-holdout 검증(in-distribution, A 무회귀)
  deploy.py        # H4r-c: 버전드 번들 교체 + 롤백
scripts/
  h4s_export_bundle.py  # 버전드로 확장(기존 수정)
  h4r_{a,b,c}_smoke.py
```

---

## H4r-a — watch→action 승격 + 지연 라벨 백필 (신호 레이어)

### 구현
- `promote.py`: drift watch 신호(DRIFT_STATE·DATASET_DRIFT_SHARE)를 종합 → **action = "조사 권고"** 생성(예: dataset_drift_share가 기준 초과 + 지속성). **자동 재학습·교체 호출 없음** — recommendation만 반환. 사람이 보고 판단.
- `backfill.py`: **지연 라벨 백필 시뮬레이션** — 라벨이 늦게 확정되는 구조를 모사(예: 예측 후 N시간 뒤 라벨 도착), 백필된 라벨로 **사후 성능 보조 측정**(utility/PR-AUC). **우산장수 한계 주석**: 개입 케이스 변질 보정 불가(개입 컬럼 없음), 성능은 보조일 뿐 주 트리거 아님.

### PASS 기준 (assert)
1. watch→action이 **조사 권고만** 반환, **자동 재학습·교체 경로 0건**(promote/backfill·retrain 모듈 AST grep: pipeline.run·deploy.swap 미호출).
2. action 승격 조건이 드리프트 종합(share+지속성), 성능 단독 아님(드리프트 주도).
3. 지연 라벨 백필 시뮬 동작, 성능 보조 측정 산출. 우산장수 태깅 미구현이 한계로 주석·문서화.

### 진행
- PASS → 자동 H4r-b. 실패 시 정지.

---

## H4r-b — 재학습 파이프라인 (B를 운영 데이터로)

### 구현
- `pipeline.py`: 사람 트리거 시 **H1~H2 재사용 재학습**(배포 조합만, 예 gru/vitals — 전체 6조합 아님). **B를 운영 데이터로**: setB를 **환자 단위 B-retrain/B-holdout 분할**(예 70/30, 환자 누수 없음). 학습 데이터 = **A-train + B-retrain**. train-only 전처리 통계 재산출(A-train+B-retrain 기준). 마스크 OFF·0-fill 금지 등 H1 규칙 연장.
- `validate.py`: **B-holdout 검증** — (a) 새 데이터(B-holdout)에서 성능, (b) **A-val 무회귀**(기존 A 성능 안 까먹음). **★ in-distribution 검증임을 명시**: A+B 학습이라 미관측 제3 분포 없음 → cross-site 일반화 주장 아님. 결과에 그 한계 기록.

### PASS 기준 (assert)
1. B 환자 단위 retrain/holdout 분할, **B-holdout ∩ B-retrain = ∅**(환자 누수 없음).
2. 재학습이 A-train+B-retrain, train-only 통계 재산출, 배포 조합만.
3. B-holdout 검증 = 새 데이터 성능 + **A-val 무회귀** 둘 다 측정.
4. **cross-site 주장 없음**: 검증 출력이 in-distribution으로 표기(미관측 제3 분포 부재 한계 명시).
5. 마스크 OFF·0-fill 부재(H1 규칙 연장).

### 진행
- PASS → 자동 H4r-c. 실패 시 정지.

---

## H4r-c — 안전 교체 + 롤백 (배포 레이어)

### 구현
- `scripts/h4s_export_bundle.py` **버전드 확장**: 고정 dir 덮어쓰기(rmtree) → **`gru_vitals@<timestamp>` 버전 dir** 생성. **이전 버전 미삭제(보존), 살아있는 번들 미덮어씀**. 원자 번들(model+통계+τ+input_dim 동일 run) 유지.
- `deploy.py`: **검증 게이트 통과(H4r-b) + 사람 승인** 후에만 교체. 교체 = ConfigMap **RUN을 새 버전 dir로 전환**(원자 스왑). **롤백**: RUN을 이전 버전 dir로 되돌림(보존돼 가능). 재학습 후 **drift reference 갱신**(새 A-train+B-retrain 분포로 — 옛 기준 잔존 방지).
- **사람 승인 시뮬레이션 체크포인트**: 교체 전 명시적 승인 단계(자동 통과 금지).

### PASS 기준 (assert)
1. **버전드 번들**: `gru_vitals@<ts>` 생성, **이전 버전 보존**(미삭제), **살아있는 번들 미덮어씀**.
2. RUN 버전 전환으로 교체(원자 스왑), **롤백(RUN 이전 버전 복귀) 동작**.
3. **검증 게이트 + 사람 승인 후에만 교체**(자동 교체 경로 0건, grep).
4. 재학습 후 drift reference 갱신, 서빙 번들과 원자 정합(skew 없음).
5. 교체/롤백이 번들 원자성 유지(불일치 번들 불가, H4s-a 원칙).

### 진행
- PASS → **H4-재학습 완료 = MLOps 루프 폐쇄.** 보고 후 멈춤.

---

## 범위 외
- 완전 자동 재학습 루프(의료 — 금지) / 실제 라벨·개입 파이프라인
- concept drift 본격 대응 / 피처·결측 ablation 2차 바퀴
- 부하 테스트(MLOps 완성 후 별도)

## 실패 모드 (정지 트리거)
- watch→action이 자동 재학습·교체 호출(human-in-loop 위반)
- B-holdout ∩ B-retrain ≠ ∅(환자 누수) / 검증이 cross-site로 과장(in-distribution인데)
- 재학습이 train-only 위반 / 마스크 켜짐 / 0-fill
- 고정 dir 덮어쓰기(버전드 아님) / 살아있는 번들 파괴 / 롤백 타깃 없음
- 검증·승인 없이 교체 / 번들 불일치(원자성 위반)
- reference 미갱신(옛 기준 잔존)
- 위 중 하나라도 → 정지·보고.

## 검토 요청 (h4_retrain_handoff_review.md 용)
- human-in-loop이 코드로 강제되나(자동 재학습·교체 0건 grep, 사람 승인 체크포인트).
- B retrain/holdout 환자 단위 분리(누수 없음), 검증이 in-distribution으로 정직 표기(cross-site 과장 없음).
- 버전드 번들이 이전 보존+살아있는 번들 미덮어씀+롤백 가능한가.
- 재학습 train-only·마스크 OFF, reference 갱신이 서빙과 원자 정합.
- 시뮬레이션 범위가 명확한가(지연 라벨·트리거 모사, 실데이터 아님).