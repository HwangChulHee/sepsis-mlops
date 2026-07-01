# H2 구현 핸드오프 — 학습 (utility · 트리 · GRU · 선정)

> **설계 근거**: [`design/h2/decisions.md`](decisions.md)(v2, 검토 PASS `e523b07`). 본 문서는 그 결정을 실행 명세로 번역.
> **워크플로우**: [`WORKFLOW.md`](../WORKFLOW.md). 자립형이며, 검토(`design/h2/handoff_review.md`) 통과 후 실행.
> **개정 이력**
> - **v2 (2026-06-28)** — 핸드오프 검토 `cec48cf`의 HOLD 3건 반영
>   - HOLD 1: H2-a에 **utility 계산 규칙 완전 인라인** — t_sepsis 유도(`첫양성+6`), U_TP/U_FN piecewise(절편·하한클리핑·FN시작점), best/inaction 정의, **14행 기대값 표 인라인**(research/03 참조 제거 → 자립).
>   - HOLD 2: HP↔τ **중첩 순서** 명시(trial마다 τ-최대화 utility, 최고 trial의 HP\*·τ\* 동시 동결) — H2-b·c.
>   - HOLD 3: 실패 모드에 **MLflow 기록 실패 / OOM·긴시퀀스 / 비유한 trial** 추가 + **동적 B-guard 기법** 명시.
>   - 명칭 정정: "12시점 표" → **14행 표**.
> - v1: 초안.

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

### 구현 (`eval/utility.py`) — 완전 인라인 정의 (research/03 참조 금지, 자립)

**상수** [확인됨: 공식 evaluate_sepsis_score.py 대조]:
`dt_early=−12h`, `dt_optimal=−6h`, `dt_late=+3h`. `u_fp=−0.05`, `u_tn=0`.

**t_sepsis 유도(치명적 — 빠지면 전 평가 6h 어긋남)**:
라벨은 발병 6h 전부터 1로 켜지므로, **`t_sepsis = (첫 양성 라벨 인덱스) + 6`** (= `argmax(label) − dt_optimal`). 보상 창은 **t_sepsis 기준**: `t_sepsis−12 ~ t_sepsis+3`. (첫 양성 인덱스를 t_sepsis로 쓰면 안 됨.)

**U_TP(n) — "위험" 예측이 맞음** (n = t_sepsis 기준 상대시간, 음수=이전):
- 상승 `−12 ≤ n ≤ −6`: `(12 + n)/6` *(n=−12→0, n=−6→+1.0; 기울기 +1/6)*. 단 **하한 클리핑 `max(·, −0.05)`** → n<−12면 −0.05.
- 하강 `−6 < n ≤ +3`: `1 − (n+6)/9` *(n=−6→+1.0, n=+3→0; 기울기 −1/9)*.
- `n > +3`: 0.
**U_FN(n) — "안전" 예측인데 놓침**:
- `n ≤ −6`: 0. `−6 < n ≤ +3`: `−(n+6)·2/9` *(n=−6→0, n=+3→−2.0; 기울기 −2/9)*. `n>+3`: 0.
**정상 환자**: "위험"=−0.05(항상), "안전"=0.

**best_predictions / inaction** (정규화용):
- `inaction` = 전부 0 예측.
- `best` = 패혈증 환자: `[max(0, t_sepsis−12) : 끝]` 구간을 1, 그 전 0; 정상 환자: 전부 0. *(매 시점 max(U_TP_if_1, U_FN_if_0)를 주는 예측 — 공식 코드 대조.)*
- **per-patient 시계열 재조립 채점**: 환자별로 시점열을 모아 U(s,t) 합산 후 전 환자 합.
- 정규화: `U_norm = (U_obs − U_inaction) / (U_best − U_inaction)`.

**검증 기대값 (research/03 14행 표 — 인라인)** — 패혈증 환자, t_sepsis 기준:
| n (시간) | U_TP(맞힘) | U_FN(놓침) |
|---|---:|---:|
| < −12 | −0.05 | 0 |
| −12 | 0 | 0 |
| −9 | +0.50 | 0 |
| −6 (최적) | +1.00 | 0 |
| −5 | +0.89 | −0.22 |
| −4 | +0.78 | −0.44 |
| −3 | +0.67 | −0.67 |
| −2 | +0.56 | −0.89 |
| −1 | +0.44 | −1.11 |
| 0 (발병) | +0.33 | −1.33 |
| +1 | +0.22 | −1.56 |
| +2 | +0.11 | −1.78 |
| +3 | 0 | −2.00 |
| > +3 | 0 | 0 |

### PASS 기준 (assert)
1. **전부음성 → `U_norm == 0.0` (±1e-6)** (= inaction).
2. **best_predictions → `U_norm == 1.0` (±1e-6)**.
3. **위 14행 기대값 표와 시점별 U_TP·U_FN 일치** (±1e-6). *(자립 — 핸드오프 내 표가 기준, research/03 참조 아님.)*
4. **t_sepsis 유도 검증**: 합성 환자(첫 양성 인덱스 k)에서 t_sepsis == k+6, 최적 보상(+1.0)이 첫 양성 인덱스(=n=−6)에 위치.
5. 상수(`u_fp=−0.05`·`u_tn=0`·기울기)가 공식 코드와 일치.

### 진행
- 5개 PASS → 자동 H2-b. 실패 → 정지·보고.

---

## H2-b — 트리 학습 (XGBoost·LightGBM) + robustness (결정 1·6·7)

### 범위
요약통계 입력으로 트리 둘 학습. HP는 **vitals_labs에서 모델별 1회 탐색→동결→두 피처셋 공통 적용**. + **robustness 체크**(vitals 자체 튜닝과 비교).

### 구현
- 입력: `features.py` 매시점 요약통계(NaN 그대로). 라벨 매시점.
- `train/tune.py`: HP 탐색 — **모델당 동일 trial 수 N**(예 N=20), 동일 search space(결정 6 범위). **HP↔τ 중첩 순서**: 각 HP trial의 점수 = *그 trial에서 τ를 A-val utility 최대화했을 때의 utility*. 모든 trial 중 최고를 골라 **(HP\*, τ\*)를 동시 동결**. **`scale_pos_weight`는 H1 산출 pos_weight로 고정(탐색 안 함 — DDD 경미 반영)**.
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
- HP 탐색: vitals_labs에서 N trial(hidden∈{32,64,128}, layers∈{1,2}, lr∈[1e-4,1e-2], dropout∈[0,0.3]). **HP↔τ 중첩**(H2-b와 동일): 각 trial 점수 = τ-최대화 A-val utility, 최고 trial의 (HP\*,τ\*) 동시 동결 → 두 피처셋 학습. **조기종료 = A-val loss**(종료 신호일 뿐, 선택은 utility).
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
- utility가 전부음성≠0.0 또는 best≠1.0 또는 14행 표 불일치 또는 t_sepsis 유도 오류
- HP가 피처셋 간 불일치 / 트리 탐색 예산 불균형
- B가 학습·튜닝·τ선정·선택에 유입
- GRU bidirectional=True / 평가에서 패딩 미제외 / loss 비유한
- τ 또는 전처리 통계 아티팩트 미저장(H3 재현 불가)
- **MLflow 기록 실패**(학습됐는데 결과 유실) → 정지·보고
- **OOM / 긴 시퀀스(최대 331h) 메모리 초과**(조용히 죽거나 스왑 무한지연) → 정지·보고. 필요 시 길이 버킷팅으로 완화(비차단 권고).
- **트리 trial 점수 비유한(NaN/inf)**(발산했는데 다음 trial로 진행) → 정지·보고
- 위 중 하나라도 → 그 토막 정지, 다음 진행 금지, 사람 보고.

### 동적 B-guard (최중요 누수 게이트 — 기법 명시)
"B 미접촉"을 prose가 아닌 런타임으로 강제: setB의 patient_id 집합을 미리 만들고, **학습·튜닝·τ선정·정규화통계 산출에 들어가는 모든 데이터의 patient_id가 그 집합과 교집합 ∅임을 각 함수 진입부에서 assert**. 위반 시 즉시 정지.

## 검토 요청 (design/h2/handoff_review.md 용)
- PASS assert가 실제로 프로그래매틱한지(특히 H2-a 14행 표, robustness 수치).
- 자립성: research/03 참조 없이 utility 수치가 인라인됐는지, 외부 레포 없이 구현 가능한지.
- 진행 로그(ETA)가 터미널+파일 양쪽에 남는지.
- H2-c가 smoke_m2m 배선과 정합하는지(단방향·마스킹).
- τ·전처리통계 아티팩트가 H3에서 B 재현 가능한 형식인지.