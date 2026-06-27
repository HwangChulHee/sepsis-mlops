# 검토 결과 — H1 (레드팀)

- **검토일**: 2026-06-27
- **대상 commit**: `d7d71b0` (검토 시점 working tree의 `design/h1_decisions.md`)
- **검토자**: Claude Code (레드팀 모드 — 동의가 아니라 반박이 목적)
- **결론**: **HOLD 3건(결정 1·4·5).** 워크플로우 §5에 따라 **구현(H1 핸드오프)으로 넘어가지 말 것.** 설계로 복귀해 아래 3건을 해소한 뒤 재검토.

> 1차 출처 직접 대조: 우승팀/상위팀 CinC 논문·코드, PhysioNet 2019 피처중요도 논문 6편, 공식 데이터/논문. 레포 대조: `smoke/data.py`, `smoke/dataset.py`, `reports/eda_findings.md`, `research/02·04`.

---

## 통과 (PASS)

### 결정 2 — 결측 처리(모델별 분기, 0-fill 금지) ✅
- **ffill 시간 방향**: `smoke/data.py:57` `feats.ffill()` = pandas는 **직전 유효값을 미래로** 전파(past→future). 미래→과거 누수 없음. 로드 시점(분할 전) ffill이지만 **환자 내** 연산이라 분할과 무관 → 누수 아님.
- **train 평균이 train split에서만**: `smoke/data.py:79-85` `compute_train_stats(train)`가 train 리스트에서만 nanmean 계산, `normalize_patient`(88-95)에서 적용. ✅
- 주의(핸드오프로): cross-site 모드에선 이 "train 평균"이 **train 사이트에서만** 계산돼야 함(결정 5와 동일 경계). 모드별 런타임 계산이면 충족.

### 결정 3 — 정규화(train-only z-score + 클리핑) ✅
- train-only z-score 구현 확인(`smoke/data.py:79-95`). 클리핑은 **고정 생리범위**(데이터 유도 아님)면 누수 없음 — DDD가 "생리적 범위"라 명시(`h1_decisions.md:56,59`).
- 데이터 정합: "범위 밖 0.1% 미만"은 `eda_findings.md:96`(범위 밖 % 최대 Resp 0.10%)과 일치. ✅
- cross-site 정규화 경계는 결정 5 HOLD에서 함께 다룸.

### 결정 6 — baseline 입력표현(윈도우 요약통계 + XGBoost) ✅ (검토요청 2건 모두 1차 출처로 해소)
- **"슬라이딩 윈도우 요약통계 + 부스팅 = 챌린지 표준"** — DDD는 이를 *2024 리뷰*로 인용(`h1_decisions.md:97`)했는데, 워크플로우 §0·§5는 "리뷰가 아니라 상위팀 코드/논문"을 요구한다. **1차 출처로 직접 재확인 완료**:
  - Separatrix(3위, Zabihi) — "Mean, minimum, maximum, median, variance, 95/99/5/1% quantiles ... last 5 and 11 hours" + **5개 XGBoost 앙상블** [CinC2019-238].
  - TASP/FlyingBubble(4위, Li/Xie) — "Statistics of the 8 vital signs in the previous 6-hour slide window: min, max, mean, std, max−min" + **LightGBM** [CinC2019-049].
  - Sepyd(2위, Du) — variance/delta over past 5–8h + **gradient boosted trees** [CinC2019-423].
  → "표준 레퍼토리"는 **과장이 아님**(상위 4팀 중 2·3·4위가 정확히 이 방식). 단 **"slope"라는 통계는 상위팀 논문에서 verbatim 확인 안 됨**(주로 variance/quantile/delta/max−min). DDD의 "기울기 등"은 "등"으로 포섭되나, 표현은 다듬는 게 정확.
- **Morrill 정정의 정확성** — DDD `h1_decisions.md:99`의 "우승팀은 단순 요약통계가 아니라 path signature + ShockIndex·BUN/CR·PartialSOFA를 씀 [확인됨]" → **사실 확인**:
  - path signature transform 사용 ✅ [CinC2019-014 §2.1].
  - ShockIndex(HR/SBP), BUN/CR, PartialSOFA 모두 구현 ✅ [CinC2019-014 §2.2.3 Table 1; repo `jambo6/sepsis_competition_physionet_2019` `src/features/derived_features.py`].
  - 모델 = **LightGBM 회귀**(utility 기반 타깃) ✅.
  - ⚠️ **단, 한 가지 보완 필요**: 우승팀은 시그니처 *외에* window min/max/count 요약통계도 **함께** 썼다(Table 2, look-back 6). DDD 문구 "단순 요약통계가 아니라"는 맞지만, "요약통계를 안 썼다"로 읽히지 않게 "요약통계에 더해 시그니처·도메인 피처까지"로 1줄 보강 권장(사실관계 정확성).
- ⚠️ 벤치마크 함정 경고(`h1_decisions.md:105`, 공식 utility 우승 ~0.36 기준 유지)는 이전 검증(03_evaluation)과 일치. ✅

### 결정 7 — 결측 마스크 기본 OFF(옵트인 자리만) ✅
- 누수 근거(마스크 = 치료행동 누수 통로) 확인 — `research/04`의 Epic/Wong 논거와 정합(이전 턴 1차 검증 완료). 효과의 과제·환자군 의존도 GRU-D vs Singh 정합.
- 주의(핸드오프로): **마스크는 ffill 이전의 raw NaN 패턴에서 생성**해야 함(ffill·mean-fill 후엔 결측 패턴이 사라짐). 결정 8이 raw NaN을 캐싱하므로 가능. off가 기본인지 플래그는 핸드오프 PASS 기준에 명시 권장.

### 결정 8 — 캐싱(16피처 NaN보존 raw 한 번) ✅
- NaN 보존 raw만 캐싱 + 정규화/ffill/분할은 런타임 → **정규화가 분할에 묶임**(train-only 안 깨짐). ✅
- 캐시에 라벨·사이트 저장은 윈도잉에 필요, Method A가 시각별로 올바르게 사용 → 미래 누수 아님.
- ⚠️ 구현 정합 주의: **스모크는 ffill을 로드 시점에 수행**(`smoke/data.py:57`)하지만, 결정 8은 ffill을 **런타임(캐시 이후)** 으로 둔다(`h1_decisions.md:123`). 풀 파이프라인은 ffill을 캐시 이후로 옮겨야 함 — 설계와 일치하나 스모크 코드와는 다르므로 핸드오프에 명시.

---

## 보류 (HOLD) — 설계 복귀 필요

### 결정 1 — 핵심 검사 6종 구성 ❌ HOLD
- **문제**: 6종(WBC·Lactate·Creatinine·BUN·Platelets·Glucose) 중 **절반만 선행연구 피처중요도로 지지**되고, 가장 자주 인용되는 추가 검사들을 **빠뜨렸다.** DDD가 명시적으로 요청한 "02·확인 과제"(`h1_decisions.md:35`, `research/02:126` 미체크)를 1차 출처로 수행한 결과:
  - **강하게 지지**: WBC(5/6편), BUN(5/6, 우승팀 BUN/CR의 근거이기도), Platelets(4/6).
  - **약함(empirically WEAK)**: Lactate(6편 중 1편만 상위 — 97.3% 결측 탓으로 추정), Creatinine(mid-pack), Glucose(불일치). *임상적으론 중요하나 이 데이터의 트리 중요도에선 상위 아님.*
  - **누락된 고중요도 검사**: **PTT**(3편 상위 — 가장 큰 누락), **HCO3/bicarbonate**(3편; 한 XGBoost 연구 #3), **Calcium**(한 XGBoost 연구 **#1**, 교차모델 상위). 그 외 PaCO2·Potassium·Chloride·(PaO2:FiO2 비) 중상위.
- **근거 출처(1차)**: Strickler *Sci Rep* 2023 (nature.com/articles/s41598-023-30091-3); Nesaragi xMLEPS LightGBM+SHAP (intechopen.com/chapters/77653); *Applied Sciences* 2025 XGBoost+SHAP (mdpi.com/2076-3417/15/19/10562 — Calcium #1·HCO3 #3); Zhao *Comput Intell Neurosci* 2021 (PMC8526252); CinC2019-317. **단, 논문 간 랭킹이 크게 불일치**(모델·결측처리 의존)함도 사실 — 어떤 단일 랭킹도 과신 금지.
- **왜 HOLD인가(실험 타당성 위협)**: H2 ablation이 "활력셋(10) vs 활력+이 6종(16)"만 비교하면, **하필 약한 6종을 골랐을 때 "검사는 도움 안 됨"이라는 틀린 결론**이 나올 수 있다. 피처셋 분기의 결론 자체가 검사 선택에 좌우된다.
- **제안**: (1) 고중요도 누락분 **PTT·HCO3·Calcium을 후보에 추가**(예: 핵심검사 6→9, 캐시는 어차피 NaN보존이라 비용 거의 없음), 또는 (2) 6종 유지하되 근거를 *피처중요도*가 아닌 **다른 축(임상 해석성 + 상대적 저결측)** 으로 명시 정정하고 Lactate/Creatinine/Glucose의 약함을 문서화. **권장은 (1)** — 캐싱이 16열이든 19열이든 비용이 거의 같으므로(결정 8), 후보를 넓혀 ablation이 진짜 신호를 찾게 하라. Glucose는 결측 82.9%로 검사 중 최저(`eda_findings.md:43`)라 데이터가용성 근거로는 유지 정당.

### 결정 4 — 윈도잉/라벨(Method A) ❌ HOLD
- **문제**: 우리 자신의 `reports/smoke_findings.md`가 **"기록-절단 누수"를 "풀 학습 최우선 라벨링 과제"로 명시**(거기 §"⚠️ Known leak", 후보 완화책으로 *마지막 k시간 드롭* / *예측-구간(prediction-horizon) 라벨링*까지 적시)했는데, 결정 4는 Method A를 그대로 택하면서 이 누수를 **언급도, 완화도, 다음 H로의 명시적 이연(defer)도 하지 않는다.** "고려한 대안"(`h1_decisions.md:74`)은 윈도우 크기와 first-vs-last 라벨만 다루고, smoke가 이미 지목한 두 완화책은 빠졌다.
- **근거 출처**: `reports/smoke_findings.md`(절단 누수·완화책 명시) + `eda_findings.md:82`(패혈증 100%가 발병 직후 우측 절단, 양성구간 마지막 ≤10h) + DDD 자신의 검토요청 `h1_decisions.md:76`("우측 절단을 신호로 누수시키지 않는지").
- **누수 분석(정직하게)**: Method A가 *피처를 통해 직접* 절단을 누수시키진 않음(ICULOS 제외, 위치 피처 없음). 하지만 (a) **양성 클래스 정의 자체가 "기록 끝 근접"** 이고 septic 기록은 발병 직후 끝나므로, 학습 분포와 **배포 분포(발병 후에도 기록 계속)가 구조적으로 다름** — 라벨 정의 인공물. (b) 윈도잉(=H1)이 바로 이 완화가 들어갈 자리다.
- **제안**: 결정 4에 최소한 (1) 절단 누수를 **명시적 항목으로 올리고**, (2) 완화책(마지막 k시간 드롭 / 예측-구간 라벨 / 비절단 코호트 비교)을 "고려한 대안"에 넣어 **H1에서 처리할지 H2/H3로 이연할지 근거와 함께 결정**하라. 그냥 침묵하면 smoke_findings와 모순.

### 결정 5 — 분할(cross-site 모드) ❌ HOLD
- **문제**: A→B/B→A 모드에서 **튜닝용 검증셋(early-stop·임계값·알림률 보정)이 어느 사이트에서 나오는지** 미정. DDD는 "val 비율·seed = 핸드오프 디테일"(`h1_decisions.md:88`)로 미뤘으나, **cross-site에서 val의 출처는 디테일이 아니라 누수 경계**다. A→B에서 임계값/조기종료를 **타깃 B에서 잡으면 명백한 타깃 누수** — 이는 결정 5 자신의 검토요청 "A→B에서 B가 학습에 전혀 안 닿는지"(`h1_decisions.md:89`)에 정면으로 걸린다. 임계값 튜닝(H3, utility/고정알림률)은 B를 만진다.
- **근거**: `h1_decisions.md:82-89`; smoke의 분할은 site-aware *random*(`smoke/data.py:62-76`)이라 cross-site 3-way(train/in-train-val/target-test) 분할이 아직 없음.
- **제안**: cross-site 모드 분할을 **3분할**로 명시 — train = train사이트의 일부, **val/튜닝 = train사이트의 나머지(절대 타깃 아님)**, test = 타깃 사이트 전체. "타깃 사이트는 최종 평가 외 어디에도(정규화·mean-fill·early-stop·임계값) 닿지 않는다"를 결정 5에 박아라(결정 2·3·8의 train 통계 경계와 한 묶음).

---

## 미결 2건에 대한 의견 (확정은 사람이)

### (A3) EtCO2 — **빼는 쪽(활력 7)을 권장** (단 저위험·캐시엔 남겨도 무방)
- **데이터**: 96.29% 결측, 37,120명(전체 92.0%)이 한 번도 미측정(`eda_findings.md:26` 및 DDD `h1_decisions.md:32`와 일치). 캡노그래피라 **사실상 인공호흡 환자에서만** 측정(MNAR).
- **판단**: GRU 경로에선 92%가 ffill로 끌 값조차 없어 **거의 전부 train평균→z-score≈0의 사실상 상수 채널** = 신호 0, 약간의 노이즈·파라미터 낭비. XGBoost 경로(NaN 그대로)에선 거의 무해·무익. "활력 8"은 데이터셋의 그룹핑일 뿐 **죽은 채널을 유지할 이유가 아니다.**
- **권장**: 모델 입력 활력에서 **EtCO2 제외(→7)**. 비용이 0인 16열 캐시(결정 8)엔 남겨 두고 *활성 피처셋에서만* 빼면 일관성·재현성 모두 유지. (참고: EtCO2의 *결측 마스크*는 인공호흡 여부를 시사해 정보적이나, 마스크는 결정 7로 기본 OFF이며 누수 우려 대상.)

### (옵션) ShockIndex 등 파생피처 — **baseline엔 넣지 말고, H2 ablation 암(arm)으로** 권장
- **사실**: 우승팀이 ShockIndex(HR/SBP)·BUN/CR·PartialSOFA를 검증 [CinC2019-014, 1차 확인]. 같은-시각 결정함수라 **누수 없음**, 생성도 쌈.
- **판단**: baseline의 가치는 *정직한 하한선*이다. 도메인 파생을 baseline에 섞으면 "단순 하한선" 서사가 흐려진다(결정 6의 "가벼운 하한선" 원칙과 충돌). 반대로 영영 안 보면 우승팀이 입증한 리프트를 놓친다.
- **권장**: **baseline = 순수 윈도우 요약통계로 고정**, 파생피처(ShockIndex·BUN/CR·PartialSOFA)는 **H2의 명시적 ablation 암**으로 추가(피처셋 분기와 같은 방식). 하한선의 정직성과 도메인 리프트 측정을 둘 다 얻는다. → "충돌하나?"에 대한 답: **baseline에 직접 얹으면 충돌, ablation 암으로 분리하면 충돌 없음.**

---

## 새로 발견한 누락·위험

1. **불균형 처리 전략이 H1에 없음** — smoke는 `pos_weight≈59.7` 사용(`reports/smoke_findings.md`), `eda_findings.md:75`는 음성:양성≈54.6. DDD H1 범위표(`h1_decisions.md:12-19`)·8개 결정 어디에도 불균형 언급 없음. pos_weight는 손실 파라미터(H2)로 봐도 되나, **다운샘플링은 전처리(H1)** 일 수 있다. **"불균형은 H2"라고 명시 이연**하거나 H1에 한 줄 추가 권장(현재는 침묵).
2. **완결성 필터 부재** — `research/02·04`가 Ding(>80% 결측 환자 제외)을 관행으로 인용하고 `eda_findings.md:110`도 "초희소 검사 드롭 고려"라 적었으나, 결정 1~8에 **윈도우/환자 완결성 필터가 없다.** ffill+mean-fill로 거의 전부 평균대치된 "유령 윈도우"가 학습에 섞일 수 있음. 의도적 무필터면 그 근거를, 아니면 필터를 결정에 추가.
3. **결정 4 통계 인용 불일치(경미)** — `h1_decisions.md:70` "8h면 환자 97.5%가 살아남고(≥12h)". 8h 윈도우의 보존율은 **100%**(`eda_findings.md:65` 길이≥8h=100%)이고 97.5%는 ≥12h 수치(`eda_findings.md:66`)다. 8h 결정의 근거로 ≥12h 수치를 댄 미스매치 → 보존율은 100%로 정정.
4. **"slope" 통계 표현(경미)** — 결정 6의 요약통계 예시 "기울기 등"은 상위팀 1차 논문에서 verbatim 확인 안 됨(주로 variance/quantile/delta). 표현만 다듬기.
5. **마스크 생성 시점·ffill 캐시 경계(경미, 핸드오프 PASS기준에 반영)** — 결정 7 마스크는 raw NaN(ffill 전)에서, 결정 8 ffill은 캐시 후 런타임. 스모크가 로드시 ffill하므로 풀 파이프라인에서 순서를 바꿔야 함.

---

## 다음 액션 (워크플로우 §5)

- **HOLD 3건(결정 1·4·5)을 `h1_decisions.md`에서 해소** 후 재검토 요청. HOLD가 남은 상태로 `h1_handoff.md` 작성·구현 금지.
- 미결 2건은 위 의견을 근거로 **사람이 확정**(EtCO2 제외 / ShockIndex는 H2 ablation 권장).
- 경미 5건은 HOLD 해소 시 함께 반영.
