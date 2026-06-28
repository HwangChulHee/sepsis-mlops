# H2 구현 핸드오프 — 학습 (utility · 트리 · GRU · 선정)

> **설계 근거**: [`h2_decisions.md`](h2_decisions.md)(v2, 검토 PASS `e523b07`). 본 문서는 그 결정을 실행 명세로 번역.
> **워크플로우**: [`WORKFLOW.md`](WORKFLOW.md). 자립형이며, 검토(`h2_handoff_review.md`) 통과 후 실행.
> **상태**: 초안 — 레드팀 검토 전.

---

## 0. 공통 규칙 (자립형)

### 환경 / 입력
- WSL2 Ubuntu, 기존 `pyproject.toml` 환경. **CPU로 충분**(트리 분 단위, GRU m2m도 CPU 가능 — 재스모크 검증).
- 입력 = H1 산출물: 캐시(`data/cache/h1/`), `src/sepsis/data/`(split·missing·normalize·sequence·features·class_balance), `src/sepsis/config.py`(featureset·GRU_BIDIRECTIONAL=False). 외부 레포 참조 금지.
- 재사용: GRU m2m 모델·loss 마스킹·masked PR-AUC는 `scripts/smoke_m2m.py`에 검증된 배선 존재 → 풀 규모로 승격.

### 분할 / 누수 (결정 3·5)
- cross_site 3분할: **A-train 학습 / A-val 튜닝·선택·τ선정 / B 봉인**(H2 전 구간 미접촉).
- 정규화 μ/σ·fill mean·pos_weight는 **A-train에서만**(H1 모듈 그대로).

### ⚠️ 진행 로그 (모든 학습 토막 공통)
모든 장시간 루프는 **진행률 + 경과 + ETA**를 **터미널과 파일 양쪽**에 남긴다.
- 파일: `logs/h2_<segment>.log` (append, 타임스탬프 `[HH:MM:SS]` 포함).
- 공통 유틸 `src/sepsis/util/progress.py` 구현: 시작시각 기록 → `done/total`로 `elapsed`·`eta = elapsed/done*(total-done)` 계산 → `print` + 파일 기록.
- **트리(H2-b)**: HP trial 단위 — `[trial k/N] elapsed 2m13s | ETA ~9m | A-val utility=0.31`.
- **GRU(H2-c)**: epoch + batch 단위 — `[epoch 4/30 | batch 1200/4960] elapsed 8m | ETA ~14m | loss=0.42`. (epoch 끝마다 A-val 지표도.)
- 각 조합·trial 시작/끝에 타임스탬프 1줄. 전체 토막 시작/끝에 총 소요.

### 커밋 / 진행 규칙
- 각 토막 완료 시 `commit & push`.
- **PASS 기준은 프로그래매틱**. 하나라도 실패 → 그 자리 정지·보고.
- **자동 진행**(assert PASS 시): H2-a → H2-b → H2-c → H2-d.
- **사람 체크포인트** ⏸: H2-d 종료(피처셋·대표 baseline 선정). + 첫 실행이므로 **각 토막 PASS 결과를 보고하고 멈춤**(H1 선례 — 자동 명세돼 있어도 사람이 토막별 확인).

### 디렉토리 (생성)
```
src/sepsis/
  util/progress.py        # 진행·ETA 로깅
  eval/utility.py         # H2-a: 공식 utility
  eval/threshold.py       # H2-b/c: A-val τ 선정
  train/tree.py           # H2-b: XGB·LGBM
  train/gru.py            # H2-c: GRU m2m (smoke 배선 승격)
  train/tune.py           # HP 탐색(공통, 동일 trial 수)
  train/select.py         # H2-d: 집계·대표 baseline
scripts/
  h2a_utility_check.py · h2b_train_trees.py · h2c_train_gru.py · h2d_select.py
logs/                     # 진행 로그
```

---

## H2-a — utility 구현 + 검증 ⭐ (결정 4)

### 범위
공식 PhysioNet 2019 utility를 구현하고 **검증**한다. 뒤 토막(b·c·d) 평가가 전부 여기 의존하므로 **맨 앞에서 검증 후 진행**.

### 구현 (`eval/utility.py`) — 인라인 정의 (research/03 참조 금지, 자립)
- 보상 창: `dt_early=-12h`(보상 시작) ~ `dt_late=+3h`(보상 끝), 최대 보상 `dt_optimal=-6h`.
- 점수: `max_u_tp=+1`, `min_u_fn=-2`, **`u_fp=-0.05`**, **`u_tn=0`** (※ 값은 공식 `evaluate_sepsis_score.py`로 대조 확인).
- 기울기: `m1=+1/6`(TP, early→optimal 상승), `m2=-1/9`(TP, optimal→late 하강), `m3=-2/9`(FN, optimal→late 하강).
- **per-patient 시계열 재조립 채점**: 점 단위가 아니라 환자별 시퀀스로 모아 시점별 U(s,t)를 합산.
- 정규화: `U_norm = (U_observed - U_inaction) / (U_best - U_inaction)`. `U_inaction` = 전부 음성, `U_best` = `best_predictions`(완벽 예측 정책의 최적).

### PASS 기준 (assert)
1. **전부음성 → `U_norm == 0.0` (±1e-6)** (inaction 정의 그 자체).
2. **best_predictions → `U_norm == 1.0` (±1e-6)** (raw 라벨이 아니라 최적 예측 기준).
3. **research/03의 검증된 12시점 표와 시점별 U(s,t) 일치**.
4. `u_fp`·`u_tn`·기울기가 공식 코드 값과 일치(대조).

### 진행
- 4개 PASS → 자동 H2-b. 실패 → 정지·보고.

---

## H2-b — 트리 학습 (XGBoost·LightGBM) + robustness (결정 1·6·7)

### 범위
요약통계 입력으로 트리 둘 학습. HP는 **vitals_labs에서 모델별 1회 탐색→동결→두 피처셋 공통 적용**. + **robustness 체크**(vitals 자체 튜닝과 비교).

### 구현
- 입력: `features.py` 매시점 요약통계(NaN 그대로). 라벨 매시점.
- `train/tune.py`: HP 탐색 — **모델당 동일 trial 수 N**(예 N=20), 동일 search space(결정 6 범위), 선택 기준 **A-val utility**. **`scale_pos_weight`는 H1 산출 pos_weight로 고정(탐색 안 함 — DDD 경미 반영)**.
- `train/threshold.py`: 각 (모델×featureset)에서 **A-val utility 최대화 τ 선정·동결**, 저장.
- 학습: XGB·LGBM 각각, vitals_labs에서 best HP → 동결 → vitals·vitals_labs 둘 다 학습. NaN 내장 처리.
- **robustness 체크**: vitals에서도 HP를 별도 탐색해 "vitals 자체최적" vs "vitals_labs HP로 돌린 vitals"의 A-val 차이를 측정·로깅(편향 크기). *결과 해석용, 메인 비교는 동결 HP.*
- MLflow: 조합별 HP·seed·A-val PR-AUC·utility·τ·전처리통계 아티팩트 기록.

### PASS 기준 (assert)
1. 4개 학습(XGB·LGBM × vitals·vitals_labs) 무오류 완주, 아티팩트 저장.
2. **HP 동결**: vitals·vitals_labs가 (모델별) **동일 HP** 사용(피처셋 간 일치 확인).
3. **공정 예산**: XGB·LGBM 탐색 trial 수·search space 동일.
4. **A-val만 사용**: 학습·튜닝·τ선정·선택에 B 미접촉(정적·동적).
5. 각 조합 A-val PR-AUC·utility·τ 기록(MLflow).
6. **robustness 로깅**: vitals 자체최적 vs 동결HP-vitals 차이 수치 산출.
7. `scale_pos_weight` 고정값 사용(탐색 안 함 확인).

### 진행
- PASS → 자동 H2-c. 실패 → 정지·보고. (진행 로그: trial 단위 ETA.)

---

## H2-c — GRU 학습 (m2m, 풀 규모) (결정 5·6)

### 범위
`smoke_m2m.py`의 검증된 m2m 배선을 **풀 규모**로 승격 + 조기종료 + HP 탐색.

### 구현 (`train/gru.py`)
- 모델: 단방향 GRU(`bidirectional=False`), per-timestep logits (smoke `GRUm2m` 재사용).
- 손실: per-timestep BCE + pos_weight, **loss 마스킹으로 패딩 제외**(smoke `run_train_epoch` 방식). 평가도 **masked PR-AUC**(패딩 제외).
- HP 탐색: vitals_labs에서 N trial(hidden∈{32,64,128}, layers∈{1,2}, lr∈[1e-4,1e-2], dropout∈[0,0.3]) → **A-val utility로 선택** → 동결 → 두 피처셋 학습. **조기종료 = A-val loss**(종료 신호일 뿐, 선택은 utility).
- τ: A-val utility 최대화로 선정·동결, 저장.
- MLflow 기록(HP·지표·τ·아티팩트).

### PASS 기준 (assert)
1. vitals·vitals_labs GRU 학습 무오류 완주, 아티팩트 저장.
2. **단방향**(`bidirectional=False`), 우측 패딩.
3. **loss·평가 양쪽에서 패딩 제외**(masked ≠ unmasked로 확인 — smoke 선례).
4. HP 동결(두 피처셋 동일), 선택 기준 utility.
5. A-val PR-AUC·utility·τ 기록.
6. A-val loss 유한(NaN/inf 아님).

### 진행
- PASS → 자동 H2-d. 실패 → 정지·보고. (진행 로그: epoch+batch ETA, loss.)

---

## H2-d — 집계·선정 (결정 1·7) ⏸ 사람 체크포인트

### 범위
6조합 결과를 모아 표로 만들고 대표 baseline 선정. 최종 피처셋·baseline 판단은 사람.

### 구현 (`train/select.py`)
- MLflow에서 6조합(XGB·LGBM·GRU × vitals·vitals_labs) A-val PR-AUC·utility 집계 → `reports/h2_results.md` 표.
- **대표 baseline 선정**: XGB·LGBM 중 A-val utility 우수자(B 미사용). 두 부스터 차이 부록.
- robustness 결과(H2-b)도 표에 병기 — 피처셋 비교의 HP 편향 크기 명시.

### PASS 기준 (프로그래매틱)
- 6조합 결과표 + 대표 baseline 선정 로그 생성. 선정이 A-val만 참조(B 미접촉).

### 진행
- ⏸ **사람 체크포인트**: 결과표(+robustness)를 보고 **(1) 메인 피처셋**(vitals vs vitals_labs), **(2) 대표 baseline**(XGB vs LGBM)을 사람이 판단. 자동 진행 아님. 통과 시 H2 완료 → H3 설계.

---

## 범위 외 (H3+)
- B 펼쳐 cross-site 최종 채점, cross-site gap 분석 (H3)
- utility 정밀 검증(우승점수 분해 TSV), 마스크 누수검증 (H3)
- 피처 엔지니어링 ablation 2차 바퀴 (이연)
- 서빙·운영 (H4)

## 실패 모드 (정지 트리거)
- utility가 전부음성≠0.0 또는 best≠1.0 또는 12시점 표 불일치
- HP가 피처셋 간 불일치 / 트리 탐색 예산 불균형
- B가 학습·튜닝·τ선정·선택에 유입
- GRU bidirectional=True / 평가에서 패딩 미제외 / loss 비유한
- τ 또는 전처리 통계 아티팩트 미저장(H3 재현 불가)
- 위 중 하나라도 → 그 토막 정지, 다음 진행 금지, 사람 보고.

## 검토 요청 (h2_handoff_review.md 용)
- PASS assert가 실제로 프로그래매틱한지(특히 H2-a 12시점 표, robustness 수치).
- 자립성: research/03 참조 없이 utility 수치가 인라인됐는지, 외부 레포 없이 구현 가능한지.
- 진행 로그(ETA)가 터미널+파일 양쪽에 남는지.
- H2-c가 smoke_m2m 배선과 정합하는지(단방향·마스킹).
- τ·전처리통계 아티팩트가 H3에서 B 재현 가능한 형식인지.