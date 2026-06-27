# sepsis-mlops

재사용 가능한 MLOps 스켈레톤을 **환자 시계열 → 실시간 패혈증 조기경보** 문제에
적용한 저장소. PhysioNet/CinC 2019 챌린지 데이터를 사용한다. 도메인 이전(domain-transfer)
MLOps 저장소 시리즈의 세 번째다(`pdm-mlops`, `chest-xray-mlops` 다음).

이 단계(step 0)는 **EDA 전용**이다 — 원시 데이터를 직접 측정해 이후 스모크 파이프라인
설계 결정(윈도우 크기, 결측 처리, 분할 전략, 클래스 불균형)을 정당화한다. 아직 모델링·전처리·인프라는 없다.

## 데이터

- **출처**: PhysioNet/CinC Challenge 2019 — *Early Prediction of Sepsis from
  Clinical Data*. 공개 데이터이며 별도 인증 불필요.
- **구조**: 환자 1명당 `.psv` 파일 1개(파이프 구분), 한 행 = ICU 1시간;
  41개 컬럼 = 40개 피처 + `SepsisLabel`.
  - `data/raw/training_setA/` — 환자 20,336명 (병원 A)
  - `data/raw/training_setB/` — 환자 20,000명 (병원 B)
- **커밋 안 함** — `data/`는 git-ignore. 재현 방법:

  ```bash
  bash scripts/download_data.sh    # 약 315 MB, PhysioNet S3 미러에서 40,336개 파일 전부 내려받음
  ```

## EDA

```bash
uv sync
uv run python scripts/eda.py
```

출력:
- 콘솔 리포트
- `reports/eda_findings.md` — 수치 + 설계 함의가 담긴 findings
- `reports/figures/*.png` — 시퀀스 길이, 첫 양성 라벨, 활력징후 히스토그램

작성 내용은 **[reports/eda_findings.md](reports/eda_findings.md)** 참고. 핵심 수치:

| | |
|---|---|
| 환자 수 (A / B / 합계) | 20,336 / 20,000 / 40,336 |
| 환자-시간(행) | 1,552,210 |
| 패혈증 환자 (양성 시간 ≥1) | 7.27% |
| 양성 환자-시간 | 1.80% (음성:양성 ≈ 55) |
| ICU 체류 중앙값 | 38시간 |
| 양성 라벨 구간 | 연속, ≤10시간, 기록 끝에서 종료 (전체의 100%) |
| 최악 결측 | 검사 >90% NaN; 활력 10–66% NaN |

## 리서치 노트

풀 학습 설계 결정은 [`research/`](research/)에서 1차 출처로 정리한다(각 문서는 주장에
출처 등급을 단다: 확인됨 / 유도 / 우리 결정 / 검증 필요).

- **[01_models.md](research/01_models.md)** — 모델 선택: XGBoost/LightGBM
  baseline → GRU 메인 → Transformer 욕심(stretch); 진짜 난제는 모델 용량이 아니라
  cross-site 일반화.
- **[02_features_missing.md](research/02_features_missing.md)** — 피처 선택과
  결측 처리: 모델별 대치, 0으로 절대 안 채움, 결측 마스크는 누수 위험으로
  기본 미사용(옵트인).
- **[03_evaluation.md](research/03_evaluation.md)** — 평가지표: PR-AUC 1차,
  공식 utility score, cross-site(A→B)를 일반화 핵심 시험으로.
- **[04_leakage_generalization.md](research/04_leakage_generalization.md)** —
  누수와 일반화: 컨닝 경로 4종과 차단/관리법, 마스크 옵트인 정책의 근거.

## 환경

WSL2 Ubuntu · `uv` + `pyproject.toml` · `pandas`, `numpy`, `matplotlib`.
