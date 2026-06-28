# H1 구현 핸드오프 — 전처리·피처·분할

> **설계 근거**: [`design/h1/decisions.md`](decisions.md) (v4, 전 항목 PASS). 본 문서는 그 결정들을 **실행 명세**로 번역한 것이다.
> **워크플로우**: [`WORKFLOW.md`](../WORKFLOW.md). 이 핸드오프는 자립형이며, 검토(`design/h1/handoff_review.md`) 통과 후 실행한다.
> **개정 이력**
> - **v3 (2026-06-27)** — 검토 v2(`0ec8aca`) 비차단 주의 반영: H1-b PASS#9 양성비율 허용오차 완화(통합 1.8%±0.3%p → plausible 1%~4% + train-only 산출 + pos_weight 유한·양수). setA-only A-train이 상단에 spurious 정지하던 오발 제거.
> - **v2 (2026-06-27)** — 핸드오프 검토 `1734bdb`의 HOLD 2건 + 게이트 크리스프 권고 반영
>   - HOLD 1: H1-b "0-fill 없음" 검증을 **정규화 후 → 대치 직후(raw 공간)**로 이동(z-score 후엔 평균fill이 0이 돼 판정 불가).
>   - HOLD 2: H1-b에 **A-train per-timestep 양성비율(pos_weight 입력) 산출·로깅 + assert** 추가(결정-구현 불일치 해소).
>   - 권고: 결측률 `≈`→허용오차, 정규화 재현→식, 재스모크 "loss 하락"→"유한" 하드게이트, 마스크 극성 명시, 트리 NaN-aware 집계, 피처셋 파생 assert, 실패모드 2건 보강.
> - v1: 초안.

---

## 0. 공통 규칙 (자립형)

### 환경
- WSL2 Ubuntu, Python(기존 `pyproject.toml`/`uv.lock` 환경). **CPU로 충분, GPU 불필요**(트리는 분 단위, GRU m2m도 1코어 epoch ~분 단위).
- 외부 레포(pdm-mlops 등) **참조 금지**. 같은 레포의 `design/h1/decisions.md`·`reports/eda_findings.md`·`smoke/`는 참조 가능. 단 핵심 명세는 본 문서에 self-contained.

### 데이터 위치
- `data/raw/training_setA/`, `data/raw/training_setB/` 에 PhysioNet 2019 `.psv`(환자 1명=파일 1개, 1행=1시간). `data/`는 `.gitignore`됨.
- setA = site A(20,336명), setB = site B(20,000명).

### 컬럼 정의 (PhysioNet 2019 헤더 기준)
- **활력 7**(EtCO2 제외): `HR, O2Sat, Temp, SBP, MAP, DBP, Resp`
- **인구통계 2**: `Age, Gender`
- **핵심 검사 9**: `WBC, BUN, Platelets, Lactate, Creatinine, Glucose, PTT, HCO3, Calcium`
- **라벨**: `SepsisLabel`
- **캐시 보존**: 위 18개 + `EtCO2`(죽은 채널이나 추후 재포함 여지로 캐시에만 보존) = **19개 피처 컬럼**
- **제외(모델·캐시 모두)**: `ICULOS, Unit1, Unit2, HospAdmTime` + 나머지 검사 17종
- **모델 입력 피처셋**: 활력셋 = 활력7+인구2 = **9** / 활력+검사셋 = **18**. (EtCO2는 캐시에만, 모델 입력 아님.)

### 커밋 규칙
- 각 토막 완료 시 `commit & push`. 메시지: `H1-a: build raw cache`, `H1-b: transform pipeline`, `H1-c: diagnostic EDA`, `H1: m2m re-smoke`.

### ⚠️ 진행 규칙 — 자동 진행 vs 사람 체크포인트
- **PASS 기준은 전부 프로그래매틱**(assert로 참/거짓). **하나라도 실패하면 그 자리에서 정지**, 다음 토막으로 진행 금지, 사람에게 실패 내용 보고.
- **자동 진행(assert PASS 시 사람 개입 없이 다음으로)**:
  - H1-a PASS → **자동** H1-b
  - H1-b PASS → **자동** m2m 재-스모크
- **사람 체크포인트(여기서 정지하고 보고)** ⏸:
  - **H1-c 진단 EDA 결과** → 사람이 "측정밀도 누수 실재 여부 → 마스크 OFF 정당화"를 **판단**. (H1-a 이후 독립 실행 가능.)
  - **m2m 재-스모크 결과** → 사람이 SMOKE PASS·손실곡선·평가 마스킹을 **확인**.
- 즉 "기계가 판정 가능한 데까진 쭉, 사람 판단이 필요한 지점에서 멈춤."

### 디렉토리 (생성 대상)
```
src/sepsis/
  config.py          # 피처셋·컬럼·상수
  data/
    cache.py         # H1-a
    split.py         # H1-b
    missing.py       # H1-b
    normalize.py     # H1-b
    sequence.py      # H1-b (GRU m2m + validity mask)
    features.py      # H1-b (트리 매시점 lookback 요약)
    class_balance.py # H1-b (pos_weight 입력: per-timestep 양성비율)
  eda/diagnostics.py # H1-c
scripts/
  build_cache.py     # H1-a 실행
  build_dataset.py   # H1-b 실행
  run_diagnostics.py # H1-c 실행
  smoke_m2m.py       # 재-스모크 실행
```

---

## H1-a — 캐시 레이어 (결정 1·8)

### 범위
4만 `.psv`를 읽어 **NaN 보존 raw 환자 배열**로 한 번 캐싱. 파일 읽기는 여기서 한 번뿐.

### 구현
- `config.py`: 위 컬럼 정의(활력7·인구2·검사9·EtCO2·라벨)를 상수로.
- `data/cache.py`:
  - 각 `.psv`를 읽어 **19개 피처 컬럼 + SepsisLabel + site(A/B)** 만 추출. **NaN은 채우지 않고 그대로 보존**(트리·GRU 두 경로 공용).
  - 환자별 배열(가변 길이 T×19) + 라벨(T,) + site + patient_id를 캐싱. 포맷은 `.npz`(환자별) 또는 parquet — 구현 재량, 단 가변 길이 보존.
  - `ICULOS, Unit1, Unit2, HospAdmTime` 및 나머지 검사 17종은 **로드하지 않음**.

### PASS 기준 (assert)
1. 캐시된 환자 수 == **40,336** (setA 20,336 + setB 20,000).
2. 각 환자 배열의 피처 컬럼 == **19개**(정의된 이름과 정확히 일치), 라벨·site·patient_id 동반.
3. `ICULOS` 등 제외 컬럼이 캐시에 **없음**.
4. **NaN 보존 검증**: 캐시 전체 검사 9종의 결측률이 `reports/eda_findings.md` 측정치와 **±0.5%p 이내**로 일치(예: WBC 93.6%, Lactate 97.3% ± 0.5%p). 0으로 안 채워졌는지 확인.
5. 라벨 값 ∈ {0,1}, 환자 내 양성 블록이 **연속이며 기록 끝에서 끝남**(우측 절단) — 위반 환자 수를 로깅(제외는 H1-b 필터에서).

### 진행
- 5개 assert PASS → **자동 H1-b**. 실패 → 정지·보고.

---

## H1-b — 변환 파이프라인 (결정 2·3·4·5·6·7)

### 범위
캐시 → 분할 → 결측/정규화 → **GRU용 m2m 시퀀스** / **트리용 매시점 요약행**. 모든 변환은 런타임(캐시에서).

### 구현 (런타임 순서 엄수 — 결정 8)
- `data/split.py` (결정 5): **환자 단위** 분할. 모드: `unified`(A·B 통합 random) / `cross_site`(A→B). cross_site는 **3분할**: `A-train` / `A-val` / `B`(전체, 봉인). **B는 train·val·정규화통계·pos_weight 어디에도 미사용**, 평가 때만.
- `data/missing.py` (결정 2):
  - **트리 경로**: NaN 그대로.
  - **GRU 경로 순서**: ① (옵트인 시) **마스크 생성 — 원본 NaN 위치에서**(극성: **1=관측됨, 0=결측**) ② forward-fill(과거→미래) ③ 잔여 빈칸 **train split 평균**. **0-fill 금지**. *마스크 기본 OFF(결정 7).*
- `data/normalize.py` (결정 3): **클리핑(생리범위, 정규화 前)** → z-score. **평균·표준편차는 train split에서만**(cross_site면 A-train).
- `data/sequence.py` (결정 4 — GRU m2m):
  - 환자 **전체 시퀀스**를 샘플 단위로. 라벨 = **매 시점 SepsisLabel**(per-timestep).
  - 배치는 **우측 패딩**(pad) + **validity mask**(실제=1, 패딩=0). `pack_padded_sequence` 등으로 가변 길이 처리.
  - **단방향(causal) GRU 전제**: 시각 t 예측에 1..t만. 양방향 금지(우측 패딩 무누수 전제).
  - validity mask는 **학습 loss + 평가 지표 양쪽**에서 패딩 시점을 제외하는 데 쓰임.
- `data/features.py` (결정 6 — 트리): 각 시각 t에서 **"t까지 최대 8h lookback 요약통계"** 한 행. 통계 = `마지막값·평균·min·max·delta(마지막−처음)·range(max−min)·variance`, **모두 NaN-aware**(관측된 값만으로 집계, `np.nanmean` 등). 초기 시점(가용<8h)은 **가용 범위만**. 전부 NaN인 lookback의 통계는 NaN 유지.
- `data/class_balance.py` (부속결정 — pos_weight 입력): **A-train(또는 통합 train)의 per-timestep 양성 비율**을 산출·로깅. many-to-many이므로 **시점 단위**로 집계(양성 시점 수 / 전체 유효 시점 수, 패딩 제외). pos_weight = neg/pos. **산출만, 적용은 H2.**

### PASS 기준 (assert)
1. **환자 누수 없음**: 세 split의 patient_id 교집합 == ∅.
2. **타깃 봉인**: cross_site에서 setB patient_id가 A-train·A-val에 **하나도 없음**.
3. **train-only 정규화**: 정규화에 쓰인 μ·σ가 train split만으로 계산됐는지 식으로 재현 — `μ_used == mean(train)` 이고 `μ_used != mean(val)` 및 `!= mean(test)`(부동소수 허용오차 1e-6). 불일치면 train 밖 데이터 유입.
4. **마스크 순서**: (마스크 ON으로 임시 검증) 마스크의 0 위치(결측)가 **원본 NaN 위치와 정확히 일치**(= ffill 이전에 생성). ffill 후 생성이면 불일치로 검출.
5. **validity mask 정합**: 각 시퀀스의 validity mask 합 == 그 환자의 실제 시점 수(패딩 제외).
6. **결측 대치 검증 (raw 공간 — 정규화 前에 판정)**: 대치 직후 GRU 입력에 **NaN 없음** AND 채운 자리 값이 **ffill값 또는 train평균과 일치**(= 0이 아님, 단 평균/실측이 우연히 0인 경우 제외). *정규화 후엔 평균fill이 0이 되어 판정 불가하므로 반드시 raw 공간에서 검사.*
7. **단방향 설정**: GRU 구성이 `bidirectional=False`.
8. **트리 정렬**: 트리 요약행 수 == 해당 split의 총 시점 수(매시점), 초기 시점이 누락되지 않음.
9. **pos_weight 입력 산출**: A-train per-timestep 양성 비율이 **train split에서만** 산출·로깅되고, **plausible 범위(1%~4%) 내**(setA-only A-train은 통합 1.8%보다 높은 ~2.1% — 비율 정확도가 아니라 train-only 산출·유효성을 검증). pos_weight = neg/pos가 **유한·양수**.
10. **피처셋 파생**: 활력셋(9)이 활력+검사셋(18)의 **컬럼 부분집합**임을 assert(집합 포함 관계).

### 진행
- 10개 assert PASS → **자동 m2m 재-스모크**. 실패 → 정지·보고.

---

## H1-c — 진단 EDA (결정 4·7) ⏸ 사람 체크포인트

### 범위
마스크 OFF 결정을 **우리 데이터로** 뒷받침할 진단. H1-a 캐시만 있으면 독립 실행 가능.

### 구현 (`eda/diagnostics.py`)
1. **측정밀도 누수 확인**: 양성 구간 vs 그 외 구간에서 **시간당 검사 측정 횟수(또는 결측률)** 비교. 발병 직전 측정이 촘촘해지는지(검사 9종별·전체).
2. **양성 시점 위치 분포**: 양성 라벨이 환자 기록 내 어디에 분포하는지(끝자락 집중 = 분포 편향 자명성 확인).

### 산출물
- `reports/h1_diagnostics.md` + 그림(`reports/figures/`). 수치·표·해석.

### PASS 기준 (프로그래매틱)
- 두 산출물 파일이 생성되고, 측정밀도 비교가 **수치로** 제시됨(양성 vs 비양성 측정률 차이).

### 진행
- ⏸ **사람 체크포인트**: 산출물 제시 후 정지. 사람이 "측정밀도가 발병 직전에 유의하게 오르나 → 마스크 OFF 정당한가"를 판단. **자동 진행 안 함.**

---

## m2m 재-스모크 (강제 항목 — 결정 4) ⏸ 사람 체크포인트

### 범위
스모크 GRU는 many-to-one·고정 8윈도우였으므로 **m2m 배선은 미검증**. 작은 규모로 m2m end-to-end를 재검증.

### 구현 (`scripts/smoke_m2m.py`)
- 소수 환자(~1,000명) subset으로: 캐시 → 분할 → 변환(H1-b) → **m2m GRU 학습(매시점 예측·loss 마스킹) → per-timestep 평가**. 배선 검증이 목적, 성능 아님.

### PASS 기준 (프로그래매틱)
1. end-to-end 무오류 완주.
2. **하드 게이트**: 학습 loss가 **유한**(NaN/inf 아님). *(loss 하락은 작은 배치에서 들쭉날쭉하므로 하드 게이트 아님 — 추세는 참고 로깅만.)*
3. **평가에서 패딩 제외 검증**: validity mask로 패딩 시점을 뺀 지표와, 안 뺀 지표가 **다름**을 확인(패딩이 가짜 음성으로 안 새는지). 패딩 시점은 지표 계산에서 제외됨이 assert로 확인.
4. 단방향 GRU, 우측 패딩 확인.

### 진행
- ⏸ **사람 체크포인트**: SMOKE PASS·손실곡선·평가 마스킹 결과를 사람이 확인. 통과 시 H1 완료 → H2 설계로.

---

## 범위 외 (H1 아님)
- 실제 풀 학습·ablation 실행 (H2)
- utility score·cross-site **평가 실행**, 마스크 누수검증 (H3)
- 절단 누수 **칼질**(k드롭/예측구간 라벨) (H3, H1-c 측정 후 판단)
- 서빙·운영 (H4)

## 실패 모드 (정지 트리거)
- 캐시 환자 수 불일치 / 제외 컬럼 잔존 / NaN이 0으로 치환됨
- 환자가 split에 걸침 / setB가 train·val에 유입
- 정규화 통계가 train 밖에서 산출 / 마스크가 ffill 뒤에 생성
- GRU bidirectional=True / 평가에서 패딩 미제외
- pos_weight 입력 미산출 / 비유한
- **긴 시퀀스(최대 331h) 메모리 초과** → 정지·보고(필요 시 truncated BPTT 등은 H2 논의, H1에선 완주 가능해야 함)
- **유령 시점(초기 전부-NaN)** 처리 미정의로 NaN이 모델 입력까지 전파
- 위 중 하나라도 → 그 토막에서 정지, 다음 진행 금지, 사람 보고.

## 검토 요청 (design/h1/handoff_review.md 용)
- PASS assert들이 **실제로 프로그래매틱**한지(애매한 기준 없는지).
- 자동 진행 토막(a→b→재스모크)에 사람 판단이 숨어있지 않은지.
- 자립성: 외부 레포 없이 이 문서만으로 구현 가능한지.
- H1-b 런타임 순서가 결정 8과 정확히 일치하는지(특히 마스크 위치).