# Q1 — 모델 리서치 (research/01_models.md)

> 목적: 풀 학습에 쓸 모델을 "데이터 기반 + 선행연구 근거"로 선택한다.
> 결론 요약(TL;DR): **baseline = XGBoost/LightGBM, 메인(운영) = GRU, 욕심(여유 시) = Transformer.**
> 그리고 이 문제의 진짜 미해결 과제는 "더 센 모델"이 아니라 **병원 간 일반화(cross-site)** 다.

---

## 0. 결정 (Decision)

| 역할 | 모델 | 한 줄 근거 | 근거 출처 |
|---|---|---|---|
| **Baseline** | XGBoost / LightGBM | 2019 표준이자 2025 리뷰에서도 강세 재확인, 결측 내장 처리, 해석 가능 | [1][3][6][10][11] |
| **메인 (운영)** | GRU | 실무가 가장 많이 채택한 시계열 계열(LSTM/GRU), 가볍고 스트리밍 추론에 자연스러움 | [7][8] |
| **욕심 (선택)** | Transformer | 2024~2025 SOTA 점수는 내지만 무겁고·스트리밍 부적합·일반화 약함 → "최신도 적용" 카드 | [5][7] |
| **공통 과제** | (cross-site 일반화) | 7년째 미해결 — 우리는 baseline부터 site A→B 외부검증으로 정량화 | [3][7] |

핵심 관점: 이 프로젝트는 "최고 점수 모델"이 아니라 **"배포 가능한 운영 시스템"**을 목표로 한다. 모델 선택 기준은 SOTA 점수가 아니라 **운영 적합성(경량·스트리밍·해석)**이다.

---

## 1. 2019 챌린지 — 무엇이 이겼나

- 공식 과제·평가(utility score)는 [1]에서 정의됨. 발병 6시간 전 조기 예측이 목표.
- 공식 utility 1위는 시계열에서 **signature 기반 피처**를 뽑은 접근 [2].
- 상위권 다수는 **gradient boosting(XGBoost/LightGBM)** 계열 [3][10].
- **현실 점수**: 공식 utility 기준 상위 5팀 평균은 약 **0.426 / 0.411 / 0.409 / 0.403 / 0.403** [4]. 즉 "정직한" 천장이 0.4대다.

### ⚠️ 2019의 결정적 교훈 — 일반화 붕괴
- 한 팀은 공개 데이터에서 utility **0.522**를 냈으나, 숨겨진(다른 병원) 데이터에선 **0.364**로 하락 [4].
- 더 강하게: 상위 5팀 전원이 out-of-sample(제3 병원) 데이터에서 **음수 utility**를 기록했다는 분석 [3]. (학습 병원 5-fold 교차검증에선 AUROC 0.868이었음에도.)
- 함의: 같은 병원에선 잘 되는데 **새 병원에선 무너진다.** 우리 EDA의 사이트 차이(A 8.80% vs B 5.71% 패혈증율)와 직결.

---

## 2. 2020~2026 — 딥러닝 / Transformer 시대

대회 이후 이 데이터로 온갖 딥러닝이 실험됨:

- **LSTM-CNN (SSP)**: 발병 4·8·12시간 전 AUROC **0.92 / 0.87 / 0.84** [8].
- **Attention + BiLSTM + CNN 하이브리드**: 4·8·12시간 창에서 기존 기법 능가 주장 [9].
- **Transformer (KA-Transformer)**: 비교 모델 중 최고, AUROC **0.98** 보고 [5].
- **앙상블/트리 계열**: 여전히 광범위하게 사용, 일부는 AUC 0.98까지 보고(단, 느슨한 설정) [10].

### ⚠️ 화려한 숫자의 함정
- AUROC 0.98 같은 값은 대개 **환자 단위/segment 단위 분류 등 느슨한(누수 끼기 쉬운) 설정**에서 나옴. 공식 utility 기준 천장(0.4대)과 차원이 다름 [4][10].
- 2025 방법론 체계적 리뷰 [7]: 6h·12h AUROC 중앙값은 **내부검증 0.886 / 0.861** → **외부(다른 병원) 검증에선 6h·12h 모두 ~0.860** 수준으로 수렴, 전체구간(full-stay) 외부검증은 **0.783**까지 하락. 결정적으로 **utility score 중앙값은 내부 0.381 → 외부 −0.164로 추락.** (음수 = 아무것도 예측 안 하느니만 못함.)
- 같은 리뷰 [7]: 시계열 알고리즘 중 **가장 자주 쓰인 건 LSTM(전체 91편 중 20편)** — Transformer가 아님. → 점수 1등(Transformer)과 실제 채택(LSTM/GRU)이 갈린다.

### 분야 전체의 병목
- 2022~2025 스코핑 리뷰 [6]: **XGBoost가 여전히 강하고, 실시간·해석 가능한 솔루션이 필요**하다고 결론. 그리고 **실제로 배포(구현)된 모델은 극소수**.
- 즉 분야의 병목은 "모델 성능"이 아니라 **"배포 부재"**. 이 빈자리가 운영 엔지니어의 기회이며, 본 프로젝트의 존재 이유.

---

## 3. 우리 선택의 근거 정리

1. **baseline을 트리(XGBoost/LightGBM)로**: 검증된 표준 [1][3][10], 결측 내장 처리로 90%+ 결측 데이터에 강함 [11], 빠르고 해석 가능 [6]. → 메인 모델이 넘어야 할 기준선.
2. **메인을 GRU로**: 실무가 가장 많이 쓴 시계열 계열 [7], 상태(state)를 이어받아 **실시간 스트리밍 추론에 자연스러움**(Transformer는 전체 시퀀스 attention이라 스트리밍에 불리). 스모크에서 이미 검증. 단 트리와 달리 GRU는 결측을 명시적으로 메워야 함 → **forward-fill + 평균 대치**(선행연구 [12][13]가 쓴 LOCF·평균 대치와 동일 계열, 우리 스모크도 환자 내 forward-fill→train 평균으로 구현).
3. **Transformer는 욕심(선택)으로**: SOTA 점수는 내지만 [5] 무겁고 일반화·운영에서 약함 [7]. "최신 구조도 적용해봤다" + (시간 되면) GRU와 비교 실험.
4. **cross-site 일반화를 처음부터 측정**: 7년째 미해결 [3][7]. site A로 학습→site B로 평가하는 외부검증을 baseline 단계부터 넣어, "내부 vs 외부 성능 격차"를 정량화. 이는 운영(배포 후 다른 환경) 관점과 직결.

---

## 4. 면접용 한 줄

> "대회 이후 Transformer까지 실험됐고 AUROC 0.98 같은 숫자도 있지만, 2025 체계적 리뷰[7]를 보면 외부 병원 검증에서 utility가 내부 0.38에서 외부 −0.16으로 무너집니다. 모델을 바꿔도 일반화는 7년째 안 풀렸습니다. 그래서 SOTA 점수 추격 대신, 실무가 가장 많이 쓰는 LSTM/GRU 계열을 운영 모델로 택하고 cross-site 일반화를 처음부터 측정하는 데 집중했습니다."

---

## 레퍼런스

> 게재지/식별자는 조사 시점 기준. 일부 항목은 저자 전체 목록 대신 대표 정보만 기재.

[1] Reyna MA, Josef CS, Jeter R, Shashikumar SP, Westover MB, Nemati S, Clifford GD, Sharma A. **"Early Prediction of Sepsis From Clinical Data: The PhysioNet/Computing in Cardiology Challenge 2019."** *Critical Care Medicine*, 48(2):210–217, 2020. DOI:10.1097/CCM.0000000000004145. (공식 챌린지 논문 / 데이터·라벨·utility 정의)

[2] Morrill J, Kormilitzin A, Nevado-Holgado A, Swaminathan S, Howison S, Lyons T. **"The Signature-based Model for Early Detection of Sepsis from Electronic Health Records in the Intensive Care Unit."** *Computing in Cardiology (CinC)*, 2019. (챌린지 공식 utility 1위)

[3] **"Stronger Baseline Models — A Key Requirement for Aligning Machine Learning Research with Clinical Utility."** arXiv:2409.12116, 2024. (상위 5팀이 out-of-sample에서 음수 utility로 일반화 실패)

[4] **"Exploring a global interpretation mechanism for deep learning networks when predicting sepsis."** *Scientific Reports*, 2023. nature.com/articles/s41598-023-30091-3. (상위 5팀 공식 utility 수치; XGBoost 0.522→0.364 일반화 하락)

[5] **"Application of the KA-Transformer model to early sepsis prediction: a hybrid network analysis based on time series data."** *Discover Applied Sciences*, 2025. DOI:10.1007/s42452-025-06628-8. (Transformer; AUROC **0.984 @ 12h 시점**, 6h 0.944 / 1h 0.962 — 비교 모델 중 최고) — 관련: Tang Y, Zhang Y, Li J. "A time series driven model for early sepsis prediction based on transformer module." *BMC Medical Research Methodology*, 24(1):23, 2024. DOI:10.1186/s12874-023-02138-6.

[6] Shanmugam H, Airen L, Rawat S. **"Machine Learning and Deep Learning Models for Early Sepsis Prediction: A Scoping Review."** *Indian Journal of Critical Care Medicine*, 29(6):516–524, 2025. PMC12186070. (2022–2025 전수; XGBoost 강세, 배포 극소수)

[7] **"A methodological systematic review of validation and performance of sepsis real-time prediction models."** *npj Digital Medicine*, 2025. nature.com/articles/s41746-025-01587-1 (PMC11973177). 91편 리뷰. (6h·12h AUROC 중앙값: **내부 0.886/0.861 → 외부 ~0.860**; full-stay 외부 0.783; **utility 중앙값 내부 0.381→외부 −0.164**; 시계열 알고리즘 중 LSTM 최다 채택 **전체 n=20**, 상위성능 서브그룹 내 n=6)

[8] Rafiei A, et al. **"SSP: Early prediction of sepsis using fully connected LSTM-CNN model."** *Computers in Biology and Medicine*, 2020. PubMed:33227577. (AUROC 0.92/0.87/0.84 @ 4/8/12h)

[9] Das PP, Wiese L, Mast M, et al. **"An attention-based bidirectional LSTM-CNN architecture for the early prediction of sepsis."** *International Journal of Data Science and Analytics*, 2024. DOI:10.1007/s41060-024-00568-z. (Attention+BiLSTM+CNN 하이브리드; 2019 PhysioNet 데이터, 4/8/12h 창에서 기존 기법 능가 주장)

[10] Ansari Khoushabar M, Ghafariasl P. **"Advanced Meta-Ensemble Machine Learning Models for Early and Accurate Sepsis Prediction to Improve Patient Outcomes."** arXiv:2407.08107, 2024. (이 논문 자체: meta-ensemble AUC 0.96 / XGBoost 0.94. 논문이 *인용한* 관련연구 — Ghias et al.(medRxiv 2022) XGBoost AUC 0.98(느슨한 설정, 6 vitals+MissForest); Barton et al.(*Comput Biol Med* 2019) GBDT AUROC 0.88 @ 6 vitals)

[11] **"Improving Early Sepsis Prediction with Multi Modal Learning."** arXiv:2107.11094. (XGBoost 결측 내장 처리, 음성 다운샘플링)

[12] Ding R, Rong F, Han X, Wang L. **"Cross-center Early Sepsis Recognition by Medical Knowledge Guided Collaborative Learning for Data-scarce Hospitals."** *ACM Web Conference (WWW '23)*, 2023. DOI:10.1145/3543507.3583989 (arXiv:2302.05702). (결측 80%↑ 환자 제외; forward-fill, 첫 시점 결측만 평균 대치)

[13] Pou-Prom C, Yang Z, Sidhaye M, Dai D. **"Development of an Early Warning System for Sepsis."** *Computing in Cardiology (CinC)* 2019, paper CinC2019-034. DOI:10.22489/CinC.2019.034. (RF+CNN 로지스틱 앙상블, 언더샘플링, LOCF 대치+선행 결측은 −1 채움)