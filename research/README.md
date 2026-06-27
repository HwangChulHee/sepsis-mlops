# research/ — 풀 학습 설계 근거 (Q1–Q4)

스모크 다음 단계(full training) 설계 결정을 **데이터 + 선행연구 1차 출처**로 정당화하는 노트 모음. 각 문서는 "읽고 이해하는 것"이 목적이라 사람 말 + 비유로 썼고, 모든 주장에 출처 등급을 단다.

## 출처 등급 (공통 범례)

| 표기 | 뜻 |
|---|---|
| **[확인됨]** | 공식 논문·챌린지 페이지·공식 코드를 직접 확인 |
| **[유도]** | 공식 파라미터로부터 계산(공식 코드로 재확인 권장) |
| **[우리 결정]** | 근거 위에서 우리가 내린 판단(논문 인용 아님) |
| **[검증 필요]** | 1차 출처로 재확인 예정/필요 |

## 문서 맵

| 문서 | 질문 | 한 줄 결론 |
|---|---|---|
| **[01_models.md](01_models.md)** | 어떤 모델을 쓰나 | baseline XGBoost/LightGBM → main GRU → stretch Transformer. 진짜 난제는 모델이 아니라 cross-site 일반화. |
| **[02_features_missing.md](02_features_missing.md)** | 뭘 먹이고 빈칸을 어떻게 채우나 | 활력+나이·성별 기본, 핵심 피검사만. 결측은 모델별로 다르게(트리=그대로, GRU=ffill). **0으로 절대 안 채움.** 결측 마스크는 누수 위험으로 **기본 미사용(옵트인)**. |
| **[03_evaluation.md](03_evaluation.md)** | "잘했다"를 무엇으로 재나 | PR-AUC 1차, 공식 utility score 정식 채점, cross-site(A→B)로 일반화 직접 측정. |
| **[04_leakage_generalization.md](04_leakage_generalization.md)** | 모델이 컨닝하는 길과 막는 법 | 누수 4종(시간 단서·치료행동·병원 스타일·환자 분할). ①④는 처리, ②③은 인지+운영으로 관리. 마스크 옵트인 정책의 근거. |

관련 측정치는 [`../reports/eda_findings.md`](../reports/eda_findings.md)(EDA), 파이프라인 배선 검증은 [`../reports/smoke_findings.md`](../reports/smoke_findings.md) 참고.

## 다음 단계 (열린 과제)

네 문서의 체크리스트에 흩어져 있던 미완 과제를 모음:

- [ ] **(02·실험)** "활력징후만" vs "핵심 피검사 추가" 성능 비교 — 피처 선택을 우리 데이터로 직접 증명
- [ ] **(02·확인)** 핵심 피검사 목록(WBC·Lactate·Creatinine·BUN·Platelets·Glucose)이 선행연구 피처 중요도와 일치하는지 교차 확인
- [ ] **(03·구현)** PR-AUC·공식 utility 구현체를 파이프라인에 연결(스모크에 PR-AUC 배선은 완료)
- [ ] **(03·확인)** 우승 점수 A/B/C 분해값을 공식 결과 TSV로 확인
- [ ] **(04·구현)** cross-site 평가(A→B / B→A) 파이프라인에 내장
- [ ] **(04·실험·누수검증)** 결측 마스크 옵트인 — 검증 A(A→B 전이)·검증 B(발병 전 한정 평가) 통과 여부
