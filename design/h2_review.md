# 검토 — H2 (레드팀 게이트)

- **대상**: `design/h2_decisions.md` (초안)
- **대상 commit**: `9e2520b`
- **검토일**: 2026-06-28
- **핵심 질문**: 이 결정들로 학습하면 **공정한 비교**가 되고, **평가가 올바르며**, **누수가 없는가**.
- **판정**: ⛔ **HOLD 3건 → 핸드오프 진행 불가.** (PASS 다수, 1차 출처 utility 정의 일치 확인)

---

## PASS

- **결정 1 (세 모델 구도)** — baseline=강한 경쟁자 논리 타당. 우승팀 LightGBM 정렬은 H1 결정 6과 정합 [확인됨: research/01, h1_decisions 결정 6]. *주의*: H1 v5에서 우승팀은 "요약통계"가 아니라 path signature+도메인파생을 썼다고 정정됨 — 결정 1·6 본문이 "트리=강한 경쟁자"로만 적혀 있어 충돌 없음. PASS.
- **결정 3 (B 봉인 = H2/H3 경계)** — 코드로 전수 확인. `split.py:19-31` `split_cross_site`의 `B`는 sealed, `split.py:51-54` `train_split_name`이 cross_site에서 `A_train`만 통계원으로 반환. 정규화 μ/σ(`normalize.py:26-35`)·fill mean(`missing.py:38-41`)·pos_weight(`class_balance.py:23-29`) 전부 train-only로 구현됨. H2가 이를 그대로 재사용하면 B 무접촉 성립. PASS. *(단 핸드오프에서 "A_train 통계를 A_val에 적용·재계산 금지" assert 필요 → 권고 5.)*
- **결정 5 (학습 설정) ↔ H1 정합** — 전제 4종 코드 확인: 단방향(`config.py:44 GRU_BIDIRECTIONAL=False`), 우측패딩+validity mask(`sequence.py:17-39`, 패딩을 **학습 loss·평가지표 양쪽에서** 제외 명시), pos_weight=A-train per-timestep·패딩 제외(`class_balance.py`). 모두 H1 결정 2·4·6·재스모크와 일치. PASS. *(331h BPTT 우려는 기우 → 권고 1.)*
- **결정 7 (대표 baseline 선정)** — A-val만 참조, utility 우선·PR-AUC 보조, B 미사용. 누수 없음. PASS. *(utility 자체가 임계값 의존 → HOLD-2 연동.)*
- **결정 8 (seed·MLflow)** — `split_cross_site(seed=42)` 결정적이라 6조합이 **동일 분할**을 공유함이 보장됨(`split.py:19`). 6조합 동일 seed로 무작위 변인 통제 성립. PASS. *(아티팩트 형식은 권고 4.)*
- **featureset 슬라이스 (결정 2 검토요청)** — `config.py:27-36,62-70` 확인: `vitals`=인덱스 0..8(9), `vitals_labs`=0..17(18), **EtCO2(인덱스 18)는 양쪽에서 배제**. `featureset_indices`가 CACHE_FEATURES prefix-slice로 정확히 분리. PASS [확인됨: config.py].
- **결정 4 방향 (PR-AUC + utility 둘 다 A-val)** — 선택 기준을 최종 목표와 정렬한다는 논리 타당, utility 정의는 1차 출처와 일치(아래 §1차 출처). 단 **임계값 정책 부재 → HOLD-2.**

---

## HOLD (수정 필요)

### HOLD-1 — 하이퍼파라미터 정책: 결정 2 ↔ 결정 6 직접 충돌 + 공정성 미조작화

- **항목**: 결정 2, 결정 6
- **문제**:
  1. **내부 모순.** 결정 2(`h2_decisions:38`)는 featureset ablation에서 "분할·결측·정규화·**하이퍼파라미터**·seed·평가는 전부 고정"이라 명시한다. 결정 6(`h2_decisions:90`)은 "하이퍼파라미터는 **미리 고정하지 않고 A-val로 탐색**"이라 명시한다. 둘은 그대로는 양립 불가.
     - featureset별로 HP를 따로 튜닝하면 → ablation 변인이 둘(featureset+HP)이 되어 결정 2가 내세운 "검사값**만** 유일 변인이라야 효과를 인과 귀속"(`:41`)이 깨진다.
     - HP를 한 번 튜닝해 두 featureset에 고정하면 → 어느 featureset에서 튜닝하는지, 다른 featureset에 불리하지 않은지가 미정.
  2. **"모델 간 튜닝 강도 공정"이 prose뿐.** 결정 6(`:90,94`)은 "튜닝 강도 절제 + 모델 간 공정(한 모델 과탐색 금지)"이라 했지만 검증 가능한 기준이 없다 — trial 수·탐색 예산·search space 크기 미지정. 한 모델이 우연히 더 넓게 탐색되면 "공정 비교"가 무너진다(task [B]가 짚은 지점).
  3. **튜닝 objective 불일치.** 결정 5는 GRU 조기종료=A-val **loss**(`:80`), 결정 4는 selection=PR-AUC+**utility**(`:62`), 결정 7은 **utility 우선**(`:103`). 무엇을 기준으로 HP를 고르는지가 세 곳에서 다르다.
- **근거**: `h2_decisions.md:38` vs `:90`; 변인통제 주장 `:41`; objective 분산 `:62,80,103`.
- **제안**:
  - (a) **HP 튜닝 단위 명문화** — 권장: 모델별로 HP를 한 번 튜닝(예: `vitals_labs`에서)하고 **동일 HP를 두 featureset에 적용**해 featureset를 유일 변인으로 유지. 또는 "featureset+HP 동시 최적" 비교로 목적을 재정의(이 경우 결정 2의 인과 귀속 주장을 철회). **둘 중 하나를 골라 명시.**
  - (b) **탐색 예산을 수치로** — 모델당 동일 trial 수(예: N=각 20)·동일 search space로 고정해 "공정"을 assert 가능하게.
  - (c) **튜닝 objective 단일화** — 권장: A-val utility(결정 7과 정렬). GRU 조기종료 loss와 최종 selection objective의 관계를 1줄로 명시(예: "조기종료는 loss, 모델/HP 선택은 utility").

### HOLD-2 — 결정 4: utility 임계값 선정 정책 부재 (평가 정의 불완전 + H3 누수 위험)

- **항목**: 결정 4 (결정 7로 전파)
- **문제**: 공식 utility score는 **이진 예측(PredictedLabel 0/1)**에 매겨진다 [확인됨: `evaluate_sepsis_score.py` — 입력이 확률+이진라벨, 점수 누적은 이진라벨 기준]. 확률→이진 변환에는 **임계값**이 필수인데 결정 4(`:62`)·결정 7(`:103`)에 임계값 선정 정책이 없다.
  - 임계값이 없으면 utility는 **정의되지 않는다**(PR-AUC는 랭킹이라 무관하지만 utility는 다름).
  - 임계값에 따라 모델 간 utility 순위가 **뒤집힐 수 있어** "공정 비교"가 성립 안 함.
  - H3에서 B를 채점할 때 임계값을 **B에서 다시 고르면 타깃 누수**(결정 3·5의 B봉인 원칙 위반). 임계값은 A-val에서 동결돼야 한다.
- **근거**: research/03:66-75(utility=이진 예측 기반, 1차 출처 일치); `h2_decisions:62,103`(임계값 무언급); 누수 규칙 `h2_decisions:50`.
- **제안**: 결정 4에 임계값 정책 추가 — 권장: **모델·featureset별로 A-val utility를 최대화하는 임계값을 선정**해 MLflow에 **동결 저장**, H3는 이 동결 임계값을 B에 **그대로** 적용(B 재튜닝 금지). "PR-AUC는 임계값 무관"임을 1줄 명시. PASS 기준에 "임계값 A-val 선정·아티팩트 저장" 추가(HOLD-3 연동).

### HOLD-3 — PASS 기준 #4·#5 비크리스프 + 누락 (프로그래매틱 assert 불가)

- **항목**: PASS 기준 #4, #5 (`h2_decisions:135-136`)
- **문제**:
  - **#5 "랜덤(PR-AUC≈0.018) 대비 유의 상회"** — "유의 상회"는 임계가 없어 assert로 떨어지지 않는다.
  - **#4 "전부음성≈0"** — 부정확. 전부음성 예측 = 무행동(inaction) 기준 그 자체라 정규화 utility는 **정확히 0.0**(≈ 아님). 또 결정 4가 약속한 "알려진 케이스 sanity"의 핵심인 **research/03의 검증된 12시점 표 대조**가 PASS에 빠짐.
  - 누락: HOLD-2의 임계값 동결, 그리고 H3가 B를 재현하려면 필수인 **전처리 통계 아티팩트**(A-train μ/σ·fill mean·pos_weight·clip bounds) 저장이 PASS에 없음.
- **근거**: `h2_decisions:135-136`; utility 정규화식 `evaluate_sepsis_score.py`(전부음성=inaction=정규화 0.0); research/03:96-118(12시점 표).
- **제안**: PASS 기준 재작성 —
  - #4 → "전부음성 예측 → 정규화 utility == 0.0(±1e-6) **and** 완벽예측 → 1.0(±1e-6) **and** research/03 12시점 표와 시점별 점수 일치".
  - #5 → 구체값(예: "6조합 전부 A-val PR-AUC ≥ 0.05, 즉 랜덤 0.018의 ~2.7배 이상" 같은 assert 가능한 하한 — 수치는 채택자가 확정).
  - 추가: "모델·featureset별 임계값과 전처리 통계(μ/σ·fill mean·pos_weight·clip)를 아티팩트로 저장(H3 B 재현용)".

---

## 1차 출처 확인 결과

### utility 정의 — ✅ research/03과 완전 일치 (1차 출처 직접 대조)

`physionetchallenges/evaluation-2019` 의 `evaluate_sepsis_score.py` raw 직접 확인:

| 파라미터 | 공식 코드 값 | research/03 표기 | 일치 |
|---|---|---|---|
| `dt_early` | **−12h** (보상 창 열림) | 발병 12h 전 | ✅ |
| `dt_optimal` | **−6h** (최대 보상) | 6h 전 +1.0 | ✅ |
| `dt_late` | **+3h** (보상 창 닫힘) | 3h 후 0 | ✅ |
| `max_u_tp` | **1** | 최대 보상 +1.0 | ✅ |
| `min_u_fn` | **−2** | 놓침 최대 −2.0 | ✅ |
| `u_fp` | **−0.05** | 헛경보 −0.05 | ✅ |
| `u_tn` | **0** | 정답 음성 0 | ✅ |
| 기울기 | `m1=1/6`, `m2=−1/9`, `m3=−2/9` | +1/6 / −1/9 / −2/9 | ✅ |
| 정규화 | `(obs − inaction) / (best − inaction)` | 동일 | ✅ |

→ **research/03의 utility 정의는 [확인됨] 유효.** DDD 결정 4의 `[검증 필요]`(임계값 외 정의 부분)는 **해소.**
- *단 핸드오프 자립성 원칙(WORKFLOW §6)상* 결정 4는 현재 research/03을 **참조만** 한다. 핸드오프에는 위 수치(dt·m1/m2/m3·정규화식)를 **인라인**할 것. utility는 **per-patient 시계열 지표**(점 단위 아님)임도 명시(권고 3).

### 하이퍼파라미터 범위 — ⚠️ 공식 문서는 default+정성가이드만, numeric RANGE는 없음

공식 문서 직접 확인 결과, **라이브러리 공식 출처에는 구체 탐색 "범위"가 없다.** default 값과 정성적 가이드("use small learning_rate with large num_iterations" 등)만 제공.

**XGBoost** (`xgboost.readthedocs.io/en/stable/parameter.html`) default:
| param | default | 범위 |
|---|---|---|
| `eta`(lr) | 0.3 | [0,1] |
| `max_depth` | 6 | [0,∞] |
| `min_child_weight` | 1 | [0,∞] |
| `subsample` | 1 | (0,1] |
| `colsample_bytree` | 1 | (0,1] |
| `gamma` | 0 | [0,∞] |
| `lambda`(L2) | 1 | [0,∞] |
| `alpha`(L1) | 0 | [0,∞] |
| `scale_pos_weight` | 1 | 불균형엔 sum(neg)/sum(pos) 권장 |

**LightGBM** (`lightgbm.readthedocs.io` Parameters / Parameters-Tuning) default·가이드: `num_leaves` 31(권장 `< 2^max_depth`), `learning_rate` 0.1, 정확도엔 "작은 lr + 큰 num_iterations", 과적합엔 `max_depth`/`min_data_in_leaf`(대용량은 수백~수천)/`feature_fraction`/`bagging_fraction`+`bagging_freq`/`lambda_l1`·`lambda_l2`/`min_gain_to_split`.

**함의 (결정 6 등급 정정 필요)**: 결정 6(`:93,97`)은 "라이브러리 공식 권장값으로 `[검증 필요]` 해소"를 기대하지만, **공식 출처가 주는 건 default일 뿐 numeric 탐색범위가 아니다.**
- default 값 자체는 `[확인됨: 공식 문서]`로 표기 가능.
- 구체 **탐색범위는 `[우리 결정]`(default 기반 관행)**으로 등급을 내려 표기해야 한다 — 그렇지 않으면 `[확인됨]`을 과대표기하는 것(WORKFLOW §3 위반).
- 제안 탐색범위(우리 결정, default 기반): lr ∈ [0.01, 0.3], max_depth ∈ {3,5,7}(또는 LGBM num_leaves ∈ {15,31,63}), n_estimators는 조기종료로 상한, subsample/colsample ∈ [0.6,1.0], L2 ∈ [0,10], scale_pos_weight=H1 산출값(±배수 소수 후보). GRU: hidden ∈ {32,64,128}, layers ∈ {1,2}, lr ∈ [1e-4,1e-2], dropout ∈ [0,0.3]. — **이 수치들은 모두 `[우리 결정]`.**

---

## 실행 전 권고 (비차단)

1. **331h GRU full-BPTT는 현실적** — RNN에서 331 timestep은 짧다(스모크가 작았을 뿐 길이 자체는 문제 아님). truncated BPTT·길이 상한 **불필요** → 결정 5의 "미결"은 닫아도 됨. 따라서 m2m 초기발병 보존(H1 결정 4)과 **충돌 없음**. 유일한 이슈는 배치 내 길이 편차로 인한 패딩 낭비 → **길이 버킷팅(length-bucketed batching)** 권장(정확성 무관, 효율만).
2. **트리 요약행 규모 현실적** — A-train ≈ 16.3K 환자 × median 39h ≈ **~63만 행** × 126열(vitals_labs) ≈ ~320MB float32. XGBoost/LightGBM에 충분 [근거: eda_findings.md:103 setA 20,336명·median 39h].
3. **utility 채점은 per-patient 재조립 필요** — utility는 환자별 시계열 지표라, 트리의 per-timestep 예측을 **환자별로 묶어** 채점해야 한다(PR-AUC 같은 flat row 집계와 다름). GRU는 validity mask로 패딩 제거 후 실제 길이로 채점.
4. **MLflow 아티팩트 형식** — native 포맷(XGBoost `.ubj`/`.json`, LightGBM `.txt`, GRU `state_dict`)으로 저장하고 **전처리 통계+임계값을 함께 로깅**해야 H3가 B를 누수 없이 재현(HOLD-2·3 연동).
5. **핸드오프 assert 추가** — "A_train 통계(μ/σ·fill mean)를 A_val/B에 **적용**하며 재계산하지 않음"을 코드 assert로.
6. **unified 모드 격리** — 참고용 unified 산출물이 cross_site 모델 선택에 절대 섞이지 않도록 저장 경로·MLflow 태그 분리.
7. **결정 1 서사 미세 정정(선택)** — 우승팀은 path signature+도메인파생(H1 v5 정정). 결정 1·6의 "우승=LightGBM" 표기는 트리 계열 정렬로만 읽혀 충돌은 없으나, "요약통계 baseline = 우승 정렬"로 오해되지 않게 1줄 주의.

---

## 다음 단계

**HOLD 3건(HP 정책·utility 임계값·PASS 기준) 수정 후 재검토.** 전부 PASS 전에는 `h2_handoff.md`로 가지 않는다(WORKFLOW §5).
