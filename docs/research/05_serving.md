# Q5 — 운영과 서빙: 모델을 실제로 굴리는 법

> 이 문서는 **읽고 이해하는 것**이 목적이라 사람 말 + 비유로 썼다.
> 출처 등급: **[확인됨]** 1차/공신력 출처 직접 확인 · **[우리 결정]** 우리 판단 · **[검증 필요]** Claude Code 재확인.

---

## 한 줄 요약

Q1~Q4가 "좋은 모델 만들기"였다면, Q5는 **"만든 모델을 실제로 굴리기"**다. 충격적 사실 — 논문 모델은 수천 개인데 **실제 배포된 건 극소수**고, 그 성패를 가른 건 **모델 성능이 아니라 운영 설계**였다(실패한 Epic vs 사망률 18% 낮춘 TREWS). 잘 굴리는 법은 다섯 가지 — ①실시간 서빙 ②알림 아껴 울리기 ③입력 변화 감시 ④조심스러운 재학습 ⑤배포 전 cross-site 점검. **이게 바로 이 프로젝트가 만드는 운영 레이어이고, "AI를 운영하는 엔지니어" 포지셔닝의 핵심이다.**

---

## 0. Q5가 답하는 질문

"모델을 병원에 갖다 놓으면 무슨 일이 일어나고, 어떻게 잘 굴리나." 모델 정확도가 아니라 **배포·운영**의 문제다.

---

## 1. 큰 발견 — 배포는 드물고, 성패는 모델이 안 가른다

많은 모델이 논문으로 기술됐지만 임상 현장에서 전향적으로 평가된 건 극소수다 [확인됨: 1, 5]. 그 극소수에서 성공과 실패가 갈렸는데 — **차이는 모델 성능이 아니라 "어떻게 배포했나"**였다.

### 실패 — Epic Sepsis Model [확인됨: 4]
미국 수백 병원에 깔렸지만: 전체 입원환자의 **18%에 알림**이 떠서 **알림 피로**를 유발하고 패혈증의 **67%를 놓쳤다**. (간호사들이 알림 카메라를 가릴 정도.) 모델 누수(Q4)도 있었지만, **과한 알림·워크플로우 무시 같은 배포 설계**가 결정적이었다.
> 교훈: 아무리 똑똑해도 **너무 시끄러우면 아무도 안 듣는다**(양치기 소년).

### 성공 — TREWS (존스홉킨스) [확인됨: 1, 2]
5개 병원에서 **진료건 약 59만**(590,736 encounters)을 모니터링. 항생제 투여 전 경보로 식별된 패혈증 환자 중 **의료진이 3시간 내 확인한 그룹은 원내 사망률이 조정 상대 18.7%(조정 절대 −3.3%) 감소**(SOFA −0.3·재원기간 −11.6h도 감소). 왜 성공했나 — 모델이 더 좋아서가 아니라:
- 의사를 **대체하지 않고 보조**("인간-기계 팀워크"). 의사가 "확인" 버튼으로 판단을 더함.
- 전체 알림의 **89%가 의료진에 의해 평가**되고 그중 **38%가 확인**됨 = 높은 채택률.
> 교훈: **의사를 대체 말고 도와라. 꼭 필요할 때만 울려라.**

### 보조 사례 — Sepsis Watch (Duke) [확인됨: 5]
임상 발현 **중앙값 5시간 전** 예측. 핵심은 "예측을 실행 가능한 임상 워크플로우로 만드는 것" — 모델 평가 + 이해관계자 참여 + 인프라 구축. 역시 **"모델보다 배포 체계"**.

> **핵심 메시지**: 리서처는 모델 점수(0.98!)를 좇지만, 실제로 생명을 구한 건 **운영 설계**였다. 그게 이 프로젝트가 만드는 부분이다.

---

## 2. 잘 굴리는 법 다섯 가지

### ① 실시간 서빙 [우리 결정]
환자 데이터가 시간마다 들어오면 **즉시** "지금 위험도 X"를 답한다. → FastAPI stateful 추론 + 스트리밍. (메인 모델을 GRU로 정한 이유 중 하나가 **가볍고 상태를 이어받아 실시간에 자연스럽다**는 것 — Q1. Transformer는 매번 전체 시퀀스를 봐 무겁다.)

### ② 알림을 아껴 울리기 [우리 결정 / 9]
Epic의 18% 알림이 피로를 낳았다. 그래서 **"이 정도만 울리겠다"는 알림률을 설계 손잡이로** 삼는다. 고정 알림률(예: α=5%)에서 "얼마나 일찍 잡나"를 평가해 임계값을 튜닝 [확인됨: 9의 fixed alert-rate 평가]. 무작정 많이 울리면 실패.

### ③ 입력 변화 감시 (성능이 아니라 입력을) ⭐ [확인됨: 6, 8]
**왜 성능이 아니라 입력인가**: 패혈증은 **정답(진짜 패혈증이었는지)이 며칠 뒤에야 확정**된다(검사·경과 관찰 필요 — Q4의 t_sepsis 정의). 그래서 **실시간 채점이 불가능**하다. 정답을 기다리면 그 며칠 동안 망가진 모델이 계속 환자를 판단한다.
- **그래서**: 정답 대신 **"들어오는 데이터가 학습 때와 달라졌나"(입력 분포)를 감시**해 미리 경보. 성능 저하 전에 먼저 안다 [확인됨: 8 — 성능만 보는 건 드리프트의 좋은 대리지표가 아니며 입력 드리프트 추적이 필요].
- **비유**: 요리사가 손님 평가(정답, 늦음)를 기다리는 대신 **재료(입력, 지금 보임)가 평소와 다른지** 먼저 본다.
- **어떻게**: 기준(학습 데이터) 분포 vs 현재 분포를 피처별로 비교(JSD/KS 등), 하루 단위(rolling window)로 추세를 봄. 예측 분포 변화(예: 위험 비율 2%→10%)도 신호. → **Evidently**(pdm에서 사용)가 정확히 이 일.
- **주의**: 너무 예민하면 또 알림 피로 → 임계값·rolling window로 추세만.

### ④ 조심스러운 재학습 — 피드백 루프 ⭐ [확인됨: 7]
**재학습이 필요한 이유**: 세상이 변하니(환자층·장비·유행) 모델이 낡는다. 최근 데이터로 다시 가르친다(낡은 지도 업데이트). 입력 감시가 경고 → 최근 데이터 수집 → 재학습 → 교체.

**함정 — 피드백 루프**: **모델이 잘할수록 자기가 배울 데이터를 망친다** [확인됨: 7].
1. 모델이 "위험!" 정확히 경보 → 2. 의사가 빨리 치료 → 3. 환자 회복 → 4. 기록엔 "패혈증 안 됨"으로 남음 → 5. 재학습 때 모델이 "내가 위험하다 한 환자가 멀쩡했네 = 내가 틀렸구나" → **자기 성공을 오답으로 학습**.
- **비유 (우산 장수)**: "비 와요" 정확히 예보 → 다들 우산 챙김 → 안 젖음 → "거봐 안 젖었잖아, 틀렸네" → 자기가 맞아서 자기를 의심하게 됨.
- **위험**: 잘하는 모델일수록 더 망가지고, 성공이 실패로 기록돼 채점도 틀려진다 [7].
- **대응 [우리 결정]**: 재학습 파이프라인은 **갖추되**, **피드백 루프 위험을 인지·문서화**한다. (개입 효과를 감안한 채점 — adherence/sampling weighting [7] — 은 고급 옵션으로 인지. 완벽 구현보다 "안다"가 핵심.)

### ⑤ 배포 전 cross-site 점검 [우리 결정 — Q4]
다른 병원에 올리기 전 "여기서도 되나" 사전 시험. A로 학습 → B로 평가(Q4). 새 환경에서 무너지는지 미리 본다.

---

## 3. 우리 프로젝트(sepsis-mlops) 매핑

| 배포 성공 조건 | 우리 컴포넌트 | pdm 재활용 |
|---|---|---|
| 실시간 서빙 | FastAPI stateful 추론 + 스트리밍 시뮬레이터 | ✅ |
| 인간-기계 팀워크 | 대시보드(위험 곡선·경보, 의사 보조) | 신규(도메인 글루) |
| 알림률 튜닝 | 임계값 + 고정 알림률 평가 | 일부 |
| 입력 드리프트 감시 | Evidently + Prometheus/Grafana | ✅ |
| 재학습(+루프 인지) | MLflow 레지스트리 | ✅ |
| 배포 전 일반화 점검 | cross-site A→B | 신규(Q4) |

→ 도구(FastAPI·Evidently·Grafana·MLflow)가 전부 "잘 굴리기"에 매핑된다. **패혈증이라는 새 옷만 입었을 뿐 뼈대는 pdm 운영 뼈대 재활용.**

---

## 4. 면접 한 줄

> "패혈증 예측에서 TREWS는 사망률을 18% 줄였고 Epic은 알림 피로로 실패했는데, 차이는 모델이 아니라 배포 설계였습니다. 그래서 실시간 서빙, 입력 드리프트 모니터링, 알림률 튜닝, 재학습까지 운영 레이어를 만들었습니다. 특히 의료는 정답 라벨이 지연돼 실시간 채점이 어려우니 성능 대신 입력 드리프트를 감시했고, 모델이 잘 작동할수록 진료를 바꿔 자기 라벨을 변형시키는 피드백 루프를 인지해 재학습을 설계했습니다."

---

## 5. Claude Code 재검증 목록

- [x] TREWS 수치 → **확인**: 590,736 진료건/5병원, 3h 확인 시 사망률 **조정 상대 −18.7%(절대 −3.3%)**, SOFA −0.3·LOS −11.6h [1]; **89% 평가·평가건의 38% 확인** [2]
- [x] Epic 수치(18%/67%) → **확인**(이전 검증): 두 수치 모두 Wong[3]·Habib[4]에 등장
- [x] Sepsis Watch "중앙값 5시간 전" → **확인**: 수치 출처는 **DIHI 프로젝트 페이지**(Sendak JMIR 논문엔 리드타임 수치 없음) [5]
- [x] 피드백 루프·adherence weighting → **확인**: Kim et al. 2025 [7] — "Adherence/Sampling Weighted Monitoring" 명시
- [x] 입력 드리프트가 성능 모니터링보다 조기 신호 → **확인**: Kore et al. Nat Commun 2024 [8] "성능 모니터링만으론 드리프트 포착 부족"
- [x] 레퍼런스 저자·게재지 세부 → **확인**(아래 갱신)
- [ ] (구현) Evidently 드리프트 + Grafana 대시보드 + 스트리밍 시뮬레이터로 드리프트 시연
- [ ] (구현) 고정 알림률 평가 + 임계값 튜닝
- [ ] (문서) 재학습 파이프라인 + 피드백 루프 위험 명시

---

## 레퍼런스

[1] Adams R, Henry KE, Sridharan A, et al. **"Prospective, multi-site study of patient outcomes after implementation of the TREWS machine learning-based early warning system for sepsis."** *Nature Medicine*, 28(7):1455–1460, 2022. DOI:10.1038/s41591-022-01894-0. (590,736 진료건/5병원; 3h 내 확인 시 사망률 조정 상대 −18.7%/절대 −3.3%, SOFA −0.3, LOS −11.6h.)

[2] Henry KE, Adams R, Parent C, et al. **"Factors driving provider adoption of the TREWS machine learning-based early warning system and its effects on sepsis treatment timing."** *Nature Medicine*, 28(7):1447–1454, 2022. DOI:10.1038/s41591-022-01895-z. (전체 알림의 89% 의료진 평가, 평가건의 38% 확인; 인간-기계 팀워크. [1]의 동반 논문.)

[3] Wong A, Otles E, Donnelly JP, et al. **"External Validation of a Widely Implemented Proprietary Sepsis Prediction Model in Hospitalized Patients."** *JAMA Internal Medicine*, 181(8):1065–1070, 2021.

[4] Habib AR, Lin AL, Grant RW. **"The Epic Sepsis Model Falls Short—The Importance of External Validation."** *JAMA Internal Medicine*, 181(8):1040–1041, 2021. (18% 알림 / 67% 놓침 / 알림 피로.)

[5] Duke Institute for Health Innovation. **"Sepsis Watch."** dihi.org/project/sepsiswatch (**임상 발현 중앙값 5시간 전 예측 — 이 수치의 출처는 DIHI 페이지**). 관련(구현 연구): Sendak MP, Ratliff W, Sarro D, et al. **"Real-World Integration of a Sepsis Deep Learning Technology Into Routine Clinical Care: Implementation Study."** *JMIR Medical Informatics*, 8(7):e15182, 2020. DOI:10.2196/15182. (※ JMIR 논문엔 리드타임/AUC 수치 없음 — 방법론 중심.)

[6] Sahiner B, Chen W, Samala RK, Petrick N. **"Data drift in medical machine learning: implications and potential remedies."** *British Journal of Radiology*, 2023. PMC10546450. (데이터 드리프트가 성능 저하의 주원인; 모니터링 + 재학습.)

[7] Kim GYE, Corbin CK, Grolleau F, Baiocchi M, Chen JH. **"Monitoring strategies for continuous evaluation of deployed clinical prediction models."** *Journal of Biomedical Informatics*, 168:104854, 2025. DOI:10.1016/j.jbi.2025.104854. PMID:40482691. (피드백 루프 — 성공한 모델이 진료를 바꿔 라벨 분포를 바꿈; **Adherence/Sampling Weighted Monitoring**.)

[8] Kore A, Abbasi Bavil E, Subasri V, et al. **"Empirical data drift detection experiments on real-world medical imaging data."** *Nature Communications*, 15:1887, 2024. DOI:10.1038/s41467-024-46142-w. (성능 모니터링만으론 드리프트 포착 부족 → 입력 드리프트 추적 필요; 표본 크기 의존.)

[9] Jin H, Lee H. **"Leakage-Aware Federated Learning for ICU Sepsis Early Warning: Fixed Alert-Rate Evaluation on PhysioNet/CinC 2019 and MIMIC-IV."** *Applied Sciences (MDPI)*, 16(6):2735, 2026. DOI:10.3390/app16062735. (고정 알림률(α=5%) 평가; A→B/B→A.)