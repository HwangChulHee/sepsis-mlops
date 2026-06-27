# 스모크 파이프라인 Findings — sepsis-mlops (Handoff 02)

> **목표: 성능이 아니라 구조 검증.** 학습 루프가 처음부터 끝까지(데이터 → 윈도잉 →
> 학습 → 평가 → MLflow) 배선 누수 없이 도는지를 ~1,000명 환자 CPU 서브셋에서 확인한다.
> 지표 *값*은 여기서 무의미하며, 오직 배선만 본다.

실행:

```bash
uv sync
uv run python -m smoke.train_smoke           # 기본값: 환자 1000명, 2 epoch, CPU
```

모듈: `smoke/data.py` (서브셋, 피처, ffill/평균대치, 정규화, 분할) ·
`smoke/dataset.py` (윈도잉 + Method-A 라벨) · `smoke/model.py` (1층 GRU) ·
`smoke/train_smoke.py` (오케스트레이션 + MLflow + assert).

## 결과: **SMOKE PASS**

기록된 실행 (seed 42): `run_id = 67ab95ad1fda477597d29c8f017f5a35`, experiment `sepsis-smoke`.

| # | PASS 기준 | 결과 |
|---|---|---|
| 1 | 환자 ~1,000명, 두 사이트 모두 로드 | 환자 1000명; 사이트 `{training_setA, training_setB}` ✅ |
| 2 | 윈도우 > 0, **양성 윈도우 > 0** | train 23,436 / val 6,691 윈도우; **양성: train 386, val 49** ✅ |
| 3 | 환자 단위 분할, **교집합 0** | train 800 (A398/B402), val 200 (A100/B100); 교집합 **0** ✅ |
| 4 | 모델 직전 **NaN 0** | train+val 모두 `assert not np.isnan(X).any()` 통과 ✅ |
| 5 | ≥1 epoch, 에러 없음 (CPU) | 2 epoch 완료 ✅ |
| 6 | train loss **유한(finite)** | epoch1 1.2724 → epoch2 1.1819 (유한) ✅ |
| 7 | val PR-AUC 계산 + 로깅 | epoch1 0.1127 → epoch2 0.2028 (값은 무의미) ✅ |
| 8 | MLflow run + params + metrics + **모델 아티팩트** | params(11) + metrics + `MLmodel`/`data/model.pth` 로깅; `mlflow.pytorch.load_model`로 재로드 ✅ |
| 9 | 단일 윈도우 추론 → prob ∈ [0,1] | 0.1578 ✅ |

### 배선 확인 (실제로 맞아야 했던 것들)
- **환자 누수 없음**: 분할은 파일 단위(= 환자 단위), 사이트 인식; train/val 환자
  id 교집합 0으로 assert.
- **표준화 누수 없음**: z-score 평균/표준편차를 **train 환자만으로** 계산해 val에
  적용. 아직 결측인 컬럼을 채우는 평균도 동일.
- **NaN 전파 없음**: 환자별 forward-fill → train 평균 대치 → `assert` NaN 0.
  (0 대치 회피 — 0은 실제 측정값이므로.)
- **라벨 정렬(off-by-one)**: 길이-8 윈도우의 타깃은 그 윈도우 **마지막** 시간의
  `SepsisLabel`. `make_windows`에서 구성상 검증됨.
- **불균형 처리**: `pos_weight = train 음성/양성 윈도우 = 59.72` — EDA 전체 데이터
  ≈55와 일관. 스칼라 텐서로 `BCEWithLogitsLoss`에 전달.

## ⚠️ 인지된 채 넘어간 누수 (확인함, 수정 안 함 — 범위 밖)

**기록-절단 누수(record-truncation leak).** EDA(`reports/eda_findings.md` §6)에서: 패혈증
환자의 기록은 **발병 근처에서 우측 절단**된다 — 양성 라벨은 **항상 마지막 기록 시간에서
끝나는 ≤10시간의 연속 블록**(패혈증 환자의 100%)이다. 스모크는 이 라벨을 그대로 사용하므로
(Method A), 모델이 진짜 생리가 아니라 *"기록이 곧 끝난다"* 라는 인공물을 양성 신호로 학습할
수 있다. 여기서의 윈도우 라벨링은 양성 윈도우를 패혈증 기록의 끝에 몰리게 만든다.

이는 스모크에서 **의도적으로 다루지 않는다**(목표는 배선). 이것은 **풀 학습의 최우선
라벨링 과제**이며, 후보 완화책:
- 각 패혈증 기록의 마지막 *k*시간을 드롭하거나, 끝에서 *k*시간 이내에 끝나는 윈도우를 제외;
- 같은-시간 라벨 대신 예측-구간(prediction-horizon) 라벨링(*m*시간 앞의 패혈증 예측);
- 비절단 코호트 정의와 비교.

## 노트 / 편차
- **피처**: 10개 = 활력 8개(`HR,O2Sat,Temp,SBP,MAP,DBP,Resp,EtCO2`) + `Age,Gender`.
  검사 / `Unit1,Unit2,HospAdmTime,ICULOS`는 스모크에서 제외(Handoff 02 §3.2).
- **MLflow 저장소**: `mlruns/` 아래 로컬 파일 스토어. mlflow 3.x는 파일 백엔드를
  `MLFLOW_ALLOW_FILE_STORE=true` 뒤에 두며(인프로세스로 설정) 모델 직렬화 기본값이
  `pt2`다; `pt2`는 GRU를 `torch.export`로 트레이스하다가 batch-dim-1 예제에서 실패하므로,
  모델은 `serialization_format="pickle"`로 로깅한다. `mlruns/`는 git-ignore
  (스크립트 재실행으로 재생성 가능) — 이 실행은 여기 문서화하며 커밋하지 않는다.
- **범위 밖(손대지 않음)**: 풀/GPU 학습, 튜닝, 절단 누수 수정, 결측 마스크/검사,
  Method B, 테스트 분할, 서빙 인프라, 모델 레지스트리 등록.
