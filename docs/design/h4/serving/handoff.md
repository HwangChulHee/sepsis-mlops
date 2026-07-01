# H4-서빙 구현 핸드오프 — 실시간 서빙 + 스트리밍 시뮬레이터

> **설계 근거**: [`docs/design/h4/serving/decisions.md`](decisions.md)(v2, 검토 PASS `b51cda6`). 실행 명세로 번역.
> **워크플로우**: [`WORKFLOW.md`](../../WORKFLOW.md). 자립형(CC가 pdm-mlops 못 봄 → 인프라 전부 인라인).
> **개정 이력**
> - **v2 (2026-06-28)** — 핸드오프 검토 `b2655d7`의 HOLD 2건 + 비차단 반영
>   - HOLD 1(API 결측 계약): pydantic 피처 `Optional[float]=None`, 누락/null→`np.nan`(0/평균 금지 — skew 진입로 차단). PASS #2에 결측 행(선두·중간) bit-동일 케이스.
>   - HOLD 2(상태 동시성/replica): **replicas=1 + 환자별 직렬화(per-pid lock)** 기본(또는 Redis 공유저장소 옵션). PASS #4를 "동시 같은-환자 요청 무결"로 강화. 실패모드에 상태저장소 장애·out-of-order·미등록 pid 추가.
>   - 비차단: 인프라 YAML 실제 인라인("pdm 기억" 문구 삭제, 표준 기술), AST grep 대상=serve/*.py, frozen 통계 immutable, 입력분포 per-feature 히스토그램.
> - v1 (2026-06-28) — 초안. 검토 비차단 7건 흡수: ffill NaN 초기화 · stateful forward+환자별 격리 · frozen-only AST grep · 입력분포 메트릭 · PASS docker build+kubectl dry-run · 시뮬레이터 B 관찰 전용 · 인프라 인라인. nit: 입력 스키마는 run 번들 featureset에서 파생.

---

## 0. 공통 규칙 (자립형)

### 환경 / 입력
- 기존 `pyproject.toml` + 서빙 의존성(fastapi·uvicorn·prometheus-client) 추가. CPU. **외부 레포 참조 금지** — 인프라는 본 문서에 인라인.
- 입력 = H2 GRU run 번들(MLflow): state_dict + 전처리통계(μ/σ·fill·clip) + τ + input_dim. 재사용 모듈: `src/sepsis/data/{missing,normalize,sequence}.py`, `train/gru.py`(stateful forward 추가).

### ★ 누수·skew 대원칙 (서빙 — 결정 3·4·6)
- **run 단위 원자 로드**: model+featureset+전처리+τ+input_dim을 **동일 run에서 통째**. 입력 스키마도 그 run의 featureset에서 파생(vitals=9 / vitals_labs=18). 불일치 번들 불가.
- **A 동결 전처리만**: μ/σ·fill·clip·τ 전부 A 동결값(로드 후 immutable). **운영 데이터로 재계산·재정규화·τ재선정 금지**.
- **causal**: 미래 미사용. 마스크 OFF(H3 확정). 0-fill 금지.
- **frozen-only 강제**: 서빙 경로에서 `fit`·`tune`·`select_threshold`·`compute_*`(통계 산출) 미호출 — AST grep(H3-b 선례), 대상=`src/sepsis/serve/*.py`.

### 진행 규칙
- 각 토막 commit & push. PASS 프로그래매틱, 실패 시 그 자리 정지·보고.
- **H4s-a PASS → 자동 b → c** (이상 없으면 쭉). 단 **a 실패 시 정지**(전처리 일관성이 토대라 깨지면 위가 무의미).
- 첫 서빙 구축이므로 전체 끝에 결과 보고 후 멈춤.

### 디렉토리 (생성)
```
src/sepsis/serve/
  bundle.py        # H4s-a: run 번들 원자 로드
  preprocess_rt.py # H4s-a: 스트리밍 전처리(ffill 상태·A 동결)
  predictor.py     # H4s-a: stateful GRU 추론(환자별 상태)
  app.py           # H4s-b: FastAPI
  simulator.py     # H4s-b: 스트리밍 시뮬레이터
  metrics.py       # H4s-b: Prometheus
deploy/
  Dockerfile · k8s/{deployment,service,configmap}.yaml   # H4s-c
scripts/ h4s_smoke.py
```

---

## H4s-a — 서빙 코어 (전처리 일관성 + stateful 추론) ★토대

### 범위
run 번들 로드 + 스트리밍 전처리(학습과 bit-동일) + stateful GRU 추론. 여기가 train-serving skew 방어선.

### 구현
- `bundle.py`: MLflow run에서 **원자 로드** — state_dict·μ/σ·fill·clip·τ·input_dim·featureset. 구성요소 출처 run_id가 모두 동일함을 로드 시 검증.
- `preprocess_rt.py`: **스트리밍 전처리 = 학습 파이프라인 재현**.
  - 환자별 상태 = 피처별 "마지막 관측값"(ffill용). **초기값 = NaN**(0/평균 금지 — skew). 선두 결측은 NaN 유지 → fill(A-train 평균)로.
  - 순서: 새 시점 → ffill(상태 갱신) → fill(A 평균) → clip(A 범위) → z-score(A μ/σ). **마스크 없음**.
  - A 동결 상수만 사용. 배치 `normalize.py`/`missing.py`와 동일 결과 보장.
- `predictor.py`: **stateful forward**. `train/gru.py` GRU에 hidden state 보존 forward 추가(현재 h_n 버림 → 반환·재주입). **환자별 hidden state 격리** + **동시성 규칙**: 같은 환자의 async 동시 요청이 hidden state read-modify-write를 경쟁하면 조용히 틀린 예측 → **환자별 직렬화(per-pid lock)**. 기본 **replicas=1**(in-memory dict; replica>1이면 파드별 상태 분리로 같은 환자가 다른 파드에 닿아 상태 불연속) — 확장 필요 시 **Redis 공유 저장소** 옵션. eval-mode, 전층 h_n. 새 시점 1스텝 전진 → 위험확률 p → 알람(p≥τ).

### PASS 기준 (assert)
1. **번들 원자성**: 로드된 model+featureset+전처리+τ+input_dim이 **동일 run_id 출처**. 불일치 시 정지.
2. **train-serving bit-동일**: 한 환자 시퀀스를 스트리밍 전처리로 처리한 결과 == 배치 파이프라인(`missing→normalize`) 결과 (±1e-6). **결측 행(선두 결측·중간 결측) 포함 케이스**로 검증. 0-fill 부재, 마스크 없음, 누락 입력이 np.nan으로 들어옴.
3. **상태관리 동치**: stateful 1스텝 전진(누적) == 전체 시퀀스 재입력 (±1e-6, causal — 미래 누수 없음).
4. **환자 격리 + 동시성**: 두 환자 교차 스트리밍 시 상태 안 섞임 **AND 같은 환자 동시 요청에도 hidden state 무결**(per-pid lock 검증). replicas=1 전제(또는 Redis).
5. **frozen-only**: 서빙 경로에 fit/tune/select_threshold/통계산출 미호출(AST grep).

### 진행
- 5개 PASS → 자동 H4s-b. **실패 시 정지·보고**(토대 깨짐).

---

## H4s-b — FastAPI + 스트리밍 시뮬레이터 + 관측성

### 구현
- `app.py` (FastAPI): `POST /predict`(환자식별자 + 현재 시점 피처 → {위험확률 p, 알람 p≥τ}), `GET /health`. **★ 결측 계약**: 피처는 `Optional[float] = None`. **누락·null → `np.nan`**(절대 0/평균 아님 — 0이면 0-fill 위반, 평균이면 ffill 우회 → 둘 다 skew). pydantic 검증, **스키마는 로드된 run featureset에서 파생**(vitals 9 / vitals_labs 18). 환자 상태는 predictor가 식별자로 관리(아래 동시성 규칙).
- `simulator.py`: 환자 기록을 **시간순 한 시점씩** `/predict`에 전송, 응답 수집. **미래 미사용**(t 시점에 1..t만). **B 사용 시 "재생"만**(선택·튜닝·재정규화 미사용 — 관찰 전용 가드).
- `metrics.py` (Prometheus): 요청 수·지연·예측확률 분포·알람률 + **입력 피처 분포 per-feature 히스토그램**(covariate 드리프트 토대 — H4-드리프트가 이 위에). `/metrics` 노출.

### PASS 기준 (assert)
1. `/predict`·`/health` 동작, 시점 입력 → 위험확률+알람 응답.
2. 입력 스키마가 run featureset 차원과 일치(vitals 9 / vitals_labs 18).
3. 시뮬레이터 시간순 재생, **미래 미사용**(causal 확인). B 사용 시 관찰 전용(재정규화·선정 없음).
4. Prometheus `/metrics` 노출(요청·지연·예측분포·알람률·**입력 피처 분포**).

### 진행
- PASS → 자동 H4s-c. 실패 시 정지·보고.

---

## H4s-c — 인프라 (Docker + K8s, 전부 인라인)

### 구현 (외부 레포 미참조 — 본 토막에서 자립 명세)
- `Dockerfile`: python 베이스 → 의존성 설치 → src·모델 아티팩트 → uvicorn 실행.
- `k8s/deployment.yaml`: 서빙 Deployment(**replicas=1** — stateful in-memory 상태 전제, 확장 시 Redis, 컨테이너 이미지, 포트, readiness/liveness probe → `/health`).
- `k8s/service.yaml`: ClusterIP/NodePort.
- `k8s/configmap.yaml`: **run 선택**(예: `RUN=gru_vitals`)·임계 등 설정. run 교체 = ConfigMap 변경.
- 매니페스트는 **표준 K8s 리소스로 본 토막에서 실제 YAML 작성**(Deployment/Service/ConfigMap). pdm-mlops 등 외부 레포 참조·인용 금지 — 표준 기술만으로 자립.

### PASS 기준 (프로그래매틱)
1. `docker build` 성공.
2. `kubectl apply --dry-run=client`로 매니페스트 유효성 통과.
3. ConfigMap의 run 설정이 서빙 번들 로드와 연결(원자성 유지).

### 진행
- PASS → H4-서빙 완료. 보고 후 멈춤 → 다음은 H4-드리프트.

---

## 범위 외 (다음)
- 드리프트 감시 본체·Grafana·알림률 (H4-드리프트)
- 재학습·피드백 루프 (H4-재학습)
- 피처셋/결측 ablation 2차 바퀴

## 실패 모드 (정지 트리거)
- 번들 불일치(구성요소 run_id 다름) / train-serving 비-동일 / 0-fill 존재 / 마스크 켜짐
- stateful≠재입력(causal 깨짐) / 환자 상태 혼선 / 같은 환자 동시 요청 레이스 / 미래 누수
- 상태저장소 장애 / out-of-order 시점 도착 / 미등록 pid 요청 / replicas>1에서 in-memory 상태 분리
- 운영 데이터로 재정규화·τ재선정 / frozen-only 위반(fit·select 호출)
- 시뮬레이터 미래 사용 / B로 선택·튜닝
- docker build 실패 / kubectl dry-run 실패
- 위 중 하나라도 → 정지·보고.

## 검토 요청 (docs/design/h4/serving/handoff_review.md 용)
- train-serving bit-동일·상태관리 동치 assert가 프로그래매틱한지(H4s-a #2·#3).
- ffill 스트리밍 NaN 초기화가 배치와 동일 결과인지.
- frozen-only AST grep이 서빙 경로를 실제 차단하는지.
- 인프라가 pdm 미참조로 자립 명세됐는지, run 교체가 원자성 유지하는지.
- 시뮬레이터 B 관찰 전용 가드가 강제되는지.