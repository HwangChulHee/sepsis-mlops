# Serving-Benchmark 구현 핸드오프 (명세부) — 2B: 벤치 하니스·실험 프로토콜·비용

> **전제**: `docs/design/serving-benchmark/decisions.md`(설계부) v5, 5라운드 검토 통과(blocker 0). 본 문서는 그 **결정 3(계측점·지표)·결정 4(공정성 통제·arm 운영·featureset 귀인)·결정 5(비용)**를 명세한다. 관측성 env 게이트(arm-2 토글)는 **handoff_2a**로 분리 — 본 문서는 그 스위치가 "존재한다"를 전제로 arm-1/arm-2를 **운영**한다.
> **워크플로우**: 검토(`handoff_2b_review.md`) 통과 → spec-writer가 §A만 보고 TDD(RED) → main이 §A+§B로 구현(GREEN). 푸시는 사람 게이트(자동 금지).
> **출제자-응시자 분리**: §A(계약·성공기준·실패모드)는 **spec-writer 전용** — src 라인 참조 없이 관측 가능한 행동으로만. §B(구현 참조)는 **main 전용**.
> **상태**: 명세부 v4 — 라운드 1~3 검토 반영(B-1·B-2·M-R2·B-R2-1·B-R3-1·M-R3-1·minor). 라운드 4 재검토 대기.

## 0. 한 줄 요약

같은 환자 스트림을 GRU·XGB 서빙에 순차로 흘려 **latency·throughput·메모리**를 수집하고, 인스턴스 요금으로 환산해 **(아키텍처 × featureset) 실배포 운영비 프로파일**을 만든다. 리플레이어(`src/sepsis/replay/`)를 벤치 하니스로 확장한다(예측 로직 불변, 계측만 추가). 정직성 3원칙: (1) latency는 **client 벽시계 + 서버 히스토그램** 둘 다 재고, 그 차(잔차)를 arm-1에서 "network"라 부르지 않는다. (2) 메모리 차이는 **per-patient state·입력차원·계측** 3기여로 갈라 귀인한다. (3) 헤드라인은 순수 아키텍처가 아니라 **결합 배포 프로파일**이며, featureset 기여는 **통제 arm(GRU/9 vs XGB/9)**으로 분리한다.

---

# §A. spec-writer 전용 — 계약·성공기준·실패모드

> spec-writer는 이 절만 읽고 TDD를 작성한다. **TDD 대상은 손으로 쓰는 마크다운 리포트가 아니라 벤치 하니스가 반환하는 구조화 결과 객체(`BenchResult`, 아래 A0 스키마)다.** 마크다운 리포트는 이 객체의 하위 렌더링일 뿐, 성공기준은 전부 **명명 필드 assert**(substring/grep 아님)로 고정한다. 서버를 실제로 띄우는 통합 시나리오는 **알려진 입력→알려진 산출**로 성공기준을 고정한다.
>
> **입력 주입 계약 (M-2)**: spec-writer는 라이브 서버·라이브 관측성 게이트(arm-2 토글)를 **구동하지 않는다.** arm-1/arm-2의 원시 측정치는 하니스에 **주입되는 알려진 입력**으로 취급하고, spec-writer는 하니스의 **집계·귀인·라벨링 로직**(주입값 → `BenchResult` 필드)만 검증한다. 주입 입력은 (i) **client 벽시계 배열**(요청별 1값), (ii) **server latency 요청별 계열**(= 요청마다 `serve_predict_latency_seconds_sum` 스크레이프의 인접 델타 배열 — 단일 누적 스칼라가 아니라 client 배열과 **같은 길이·같은 요청집합**의 배열, B-R3-1), (iii) `/proc` RSS 값이다. **client·server 두 latency 계열은 반드시 동일 요청집합**이라야 `steady_state_start` 슬라이스와 잔차 페어링이 성립한다. 라이브 arm-2 실행으로 실제 값을 채우는 것(end-to-end GREEN)은 handoff_2a 게이트 선행에 의존한다(A9 전제).

## A0. 구조화 결과 객체 스키마 (`BenchResult`) — TDD 계약면

벤치 하니스는 아래 필드를 가진 객체(dataclass 또는 동형 JSON)를 반환한다. **필드 이름·중첩·enum 값이 관측 계약**이며, 성공기준 A1~A7은 이 필드에 대한 assert로 재작성된다. (필드가 어느 함수에서 나오는지·직렬화 포맷은 §B 소관 — 여기선 이름·타입·불변식만.)

> **load-bearing vs 분포 참고 (B-R2-1)**: 정직성 논증(A2 `residual`·`tax`)과 A2 성공기준은 **버킷 무관 평균**(`client_mean`·`server_mean`, `serve_predict_latency_seconds`의 `_sum/_count`) 위에서만 성립한다 — 히스토그램 버킷 정밀도에 **무관**. 서버/클라 **분위수(p50/p95/p99)는 분포 리포트 용도**로만 유지하며 load-bearing에서 내린다(A1-b sanity 체크 제외). 이렇게 하여 A2 residual/tax는 어느 핸드오프의 버킷 설정에도 의존하지 않는다(A9 고아 의존 제거).

```
BenchResult
  gru:  ModelBench          # 배포 arm 모델 1
  xgb:  ModelBench          # 배포 arm 모델 2
  headline_label: str       # enum — A6-a
  control_arm:   ControlArm # presence 필수 — A6-b
  attribution:   list[Attribution] # A6-c — 지표별 featureset vs 아키텍처 기여 분해 (지표당 1개, 비어있지 않음)
  cost:          CostResult # A7 / m-3

ModelBench                  # 한 모델의 arm-1/arm-2 latency·throughput·memory
  arm1: ArmLatency          # 부가 계측 ON (배포 계측 프로파일)
  arm2: ArmLatency          # 부가 계측 OFF (순수 추론 프로파일)
  tax:  float               # == arm1.residual − arm2.residual  (A2-c 불변식, 평균 기반)
  boot_latency: float       # presence — 부팅 비용(모델 로드+GRU 캘리브레이션) 정상상태와 분리 (A5-c, m-R2-1)
  steady_state_start: int   # presence — 정상상태 컷 index, 비수렴 시 −1 = run FAIL (A5-c, M-R2-1/m-R2-1)
  throughput: Throughput    # A3
  memory: MemoryBreakdown   # A4
  stateless_claim: bool     # 계약값 == False  (A4-c, grep 아님)

ArmLatency
  client:  Quantiles        # 벽시계 분위수 — 분포 리포트 용도(load-bearing 아님, B-R2-1)
  server:  Quantiles        # 서버 히스토그램 '버킷보간' 분위수 — 분포 리포트 용도(load-bearing 아님, B-R2-1)
  client_mean: float        # client 벽시계 배열의 [steady_state_start:T] 슬라이스 산술평균 — 버킷 무관 (B-R2-1)
  server_mean: float        # server latency 요청별 계열(_sum 인접 델타)의 [steady_state_start:T] 슬라이스 평균
                            #   — client_mean과 동일 요청집합(B-R3-1). 단일 누적 _sum/_count 아님. 버킷 무관.
  residual: float           # == client_mean − server_mean — 동일 정상상태 집합 위 버킷 무관 평균 잔차 (load-bearing, B-R2-1/B-R3-1)
  residual_label: str       # enum, arm 별 고정값 — A2

Quantiles: { p50: float, p95: float, p99: float }   # 분포 참고용, 정직성 논증(A2)은 residual=평균 기반

Throughput                  # A3 / M-3
  n_streams: int            # 동시 스트림 수(설정 가능)
  unique_patient_ids: int   # == n_streams (중복 pid 금지 — orchestrator ValueError, §B1)
  req_per_sec: float
  wall_seconds: float

MemoryBreakdown             # A4-b
  rss:  float               # A4-a
  peak: float               # A4-a
  instrumentation: float    # == rss_arm1 − rss_arm2  (값 검증되는 유일 기여, A4-b(3))
  state:      float | None  # presence-only — 동일 featureset·환자수 sweep RSS 기울기 (M-4)
  input_dim:  float | None  # presence-only — 동일 아키텍처 featureset delta(예 XGB/9→XGB/18 RSS차)로만 (M-4)

ControlArm                  # A6-b — presence-only 계약
  gru9: ModelBench          # GRU/vitals9 — 접근 경로 .memory.rss (m-R2-2). 배포 GRU와 동일 featureset(vitals9)이라
                            #   gru9 == 배포 gru의 별칭/재기재(GRU는 배포=통제 동일 featureset, m-R2-3)
  xgb9: ModelBench          # XGB/vitals9 (동일 featureset 통제) — 접근 경로 .memory.rss (m-R2-2)

Attribution                 # A6-c — 지표별 featureset vs 아키텍처 기여 분해 (M-R2-2)
  featureset_contrib: float # 값 검증 — 동일 아키텍처 내 9→18 delta (XGB: xgb.M − control_arm.xgb9.M)
                            #   부호 주의(m-R3-1): memory.rss 지표에선 featureset_contrib(=xgb−xgb9) == −input_dim
                            #   (memory.input_dim은 xgb9−xgb로 정의) — 같은 델타의 반대 부호. presence라 값충돌 없음.
  arch_contrib:       float # presence-only — 동일 featureset 아키텍처차 (gru9.M vs xgb9.M), NB2 state 형태차 섞임
  metric:             str   # 어느 지표의 분해인지 (예 "memory.rss", "residual") — 렌더링 라벨

CostResult                  # A7 / m-3 — 손 표가 아니라 구조화 산출
  target_throughput:        float   # 목표 처리량(예 병동 N환자/시간)
  per_instance_throughput:  float   # 측정 기반 인스턴스당 처리량
  instance_count:           int     # == ceil(target_throughput / per_instance_throughput)
  price_per_hr:             float
  cost_per_hr:              float    # == instance_count × price_per_hr
  instance_type:            str      # 출처 명시 필드
  price_source:             str      # 요금 출처 URL/문서(재현성)
```

### enum 고정값 (동등성 검사 — substring 아님)

| 필드 | arm-1 / 배포 | arm-2 | 금지값 |
|---|---|---|---|
| `ArmLatency.residual_label` | `"client_server_residual"` | `"network_plus_serialization"` | arm-1에서 `"network"` (단독) |
| `BenchResult.headline_label` | `"combined_deployment_profile"` | — | `"pure_architecture"` |

성공기준은 이 enum에 **동등성 assert**(`== "client_server_residual"`, `!= "network"`)로 검사한다. arm-2 라벨이 `"network_plus_serialization"`을 포함해도 arm-1 필드는 별도 값이라 거짓양성이 원천 차단된다(B-1).

## A1. latency 수집 — 두 계측점 병행

리플레이어가 각 `/predict` 요청을 보낼 때 **client 벽시계**(요청 직전~응답 직후)를 잰다. 동시에 서버가 노출하는 **서버 내부 latency 히스토그램**(`serve_predict_latency_seconds`, `/metrics`)도 수집한다.

- **성공기준 A1-a (load-bearing 평균 + 분포 분위수 둘 다 있음)**: 각 모델의 **load-bearing 평균** `arm.client_mean`·`arm.server_mean`이 채워진다(둘 다 **버킷 무관**, 어느 하나라도 `None`이면 FAIL). 두 평균은 **동일 요청집합의 정상상태 슬라이스 `[steady_state_start:T]`** 위에서 계산된다 — `client_mean`은 client 벽시계 배열의, `server_mean`은 server latency 요청별 계열(주입 계약 (ii))의 같은 슬라이스 평균(B-R3-1). 추가로 **분포 리포트용** `arm.client`·`arm.server`(각 p50/p95/p99)를 병기한다 — 이 분위수는 분포 참고값일 뿐이며 A2 정직성 논증에 쓰이지 않는다. 서버 분위수가 버킷 격자에 스냅되어도(§B2) load-bearing 지표(평균 기반 residual/tax)는 영향받지 않는다(B-R2-1).
- **성공기준 A1-b (client ≥ server sanity, EPS 허용 — 분위수 sanity)**: 같은 요청 집합에서 `arm.client.pX ≥ arm.server.pX − EPS`(각 X∈{50,95,99})가 성립한다. **이는 분포 분위수에 대한 sanity 체크**(load-bearing 아님)다 — client(벽시계, 서버+네트워크 포함)가 server(순수 추론)보다 낮으면 계측 오류. `arm.server`는 히스토그램 '버킷보간' 추정량, `arm.client`는 벽시계 추정량이라 추정량 종류가 달라 버킷 경계서 server가 근소 초과 가능하므로 `EPS`(하니스 상수, 권장 기본 = 서버 히스토그램 최소 유효 버킷 폭)를 허용오차로 둔다. `EPS` 초과로 위반해야만 sanity FAIL, eps 이내 초과는 경고로 강등. (default 버킷이면 server 분위수가 sub-25ms에서 격자 스냅되지만, 이는 sanity 체크의 관용 폭을 넓힐 뿐 load-bearing residual과 무관.)

## A2. 잔차 정직성 — arm에 따라 다르게 라벨링 (load-bearing)

`client 벽시계 평균 − 서버 latency 평균`을 `ArmLatency.residual`(`client_mean − server_mean`, **버킷 무관**)로 계산하고, `residual_label` enum으로 라벨링한다. **두 평균이 동일 요청집합(정상상태 슬라이스) 위에 있으므로 `mean(client) − mean(server) = mean(client − server)`로 per-request 페어링이 성립**한다 — client·server가 같은 요청집합이라는 전제(주입 계약 (ii)·B-R3-1)가 이 항등식의 성립 조건이다. 라운드1의 분위수차 unpaired proxy 문제(m-1)와 라운드2의 fine 버킷 의존(B-R2-1)이 이로써 함께 해소되고, arm1−arm2 tax 차분도 정확해진다(분위수차 proxy의 음수 노이즈 없음).

- **성공기준 A2-a (arm-1에서 network 금지 — enum 동등성)**: `gru.arm1.residual_label == "client_server_residual"` 그리고 `!= "network"`. `xgb.arm1.residual_label`도 동일. arm-1의 라벨이 `"network"`(금지값)면 FAIL. (substring이 아니라 필드 동등성이라, arm-2 라벨이 network를 포함해도 무관.)
- **성공기준 A2-a2 (residual 정의 불변식 — 일급 기준, M-R3-1)**: `arm.residual == arm.client_mean − arm.server_mean`(부동소수 허용오차 내, 각 arm). residual은 스키마 주석이 아니라 이 assert로 고정된다 — server latency가 (단일 누적이 아니라) client와 동일 정상상태 슬라이스의 요청별 계열 평균임을 spec-writer가 주입값으로 RED 검증한다.
- **성공기준 A2-b (network 추정은 arm-2에서만)**: `arm2.residual_label == "network_plus_serialization"`. arm-2 잔차만 network 추정으로 해석된다.
- **성공기준 A2-c (핸들러 후처리분 = 차분 불변식)**: `model.tax == model.arm1.residual − model.arm2.residual`(부동소수 허용오차 내). tax는 "부가 계측 세금" 필드로 별도 존재하며 network에 흡수되지 않는다.

> **근거**: 서버 latency 지표는 순수 추론 구간만 재고(추론 이후의 핸들러 부가작업은 이 지표에 미포함 — 구현 경계는 §B), 핸들러의 부가작업(피처 루프·드리프트 적재)은 client 벽시계에만 잡혀 `client_mean − server_mean` 잔차에 섞인다. **두 평균이 동일 요청집합의 정상상태 슬라이스라야** per-request 페어링이 정확히 성립하고(B-R3-1) 이 귀속이 분위수 proxy보다 깨끗하다 — 만약 server가 프로세스 수명 누적(warmup 포함)이고 client가 정상상태 슬라이스면 집합이 어긋나 GRU 첫-predict 워밍업이 server_mean만 비대칭으로 부풀려 귀속이 오염된다(그래서 주입 계약 (ii)가 server를 요청별 계열로 규정). 이 부가작업은 GRU 9 vs XGB 18로 **비대칭**이라, arm-1 잔차를 통째로 network라 부르면 XGB network가 체계적으로 부풀려진다. tax(= arm1.residual − arm2.residual)는 부가계측만 남기고 network+직렬화는 양 arm residual에 공통이라 상쇄된다.

## A3. throughput — 동시 부하

- **성공기준 A3**: `model.throughput.n_streams`(설정 가능한 동시 스트림 수)로 부하를 걸어 `req_per_sec`(또는 `wall_seconds`)를 각 모델별로 채운다. 단일 스트림 latency와 별개 필드다. **불변식**: `throughput.unique_patient_ids == throughput.n_streams` — 동시 스트림은 **유일 patient_id N개**로 구성되어야 한다(중복 pid는 서버 hidden state를 섞어 시작 전 ValueError, §B1). "N 설정 가능"은 곧 "유일 pid N개 생성"을 의미한다.

## A4. 메모리 — RSS/peak + 3기여 분해

- **성공기준 A4-a (측정 존재)**: `memory.rss`·`memory.peak`가 각 서빙 프로세스에 대해 채워진다(`None`이면 FAIL).
- **성공기준 A4-b (3기여 분해 — 값 검증 vs presence 정직 구분)**: 메모리 차이를 하나의 숫자로 뭉뚱그리지 않고 세 필드로 나눈다.
  - **(3) 계측 부속물** `memory.instrumentation` — **값 검증**: `== rss_arm1 − rss_arm2`(같은 모델의 arm-1 RSS − arm-2 RSS). 동일 모델·동일 featureset·동일 환자수에서 계측 토글만 다르므로 깨끗한 차분.
  - **(2) 입력차원** `memory.input_dim` — **동일 아키텍처 내 featureset delta로만** 실측 분리(예: XGB/9 RSS − XGB/18 RSS = `control_arm.xgb9.memory.rss` vs `xgb.memory.rss` 배포 arm; `control_arm.*`는 `ModelBench`라 `.memory.rss`로 접근). GRU/9 vs XGB/9로 재면 per-patient state 형태차(hidden state vs lookback 버퍼)가 섞이므로(NB2) **교차-아키텍처 분리 금지**. 이 축을 아키텍처 간 단일 값으로 검증할 수 없을 때는 `presence-only`(필드 존재 + 산출 공식 기재)로 정직 표시.
  - **(1) per-patient state** `memory.state` — **동일 featureset·환자 수 sweep의 RSS 기울기**(ΔRSS/Δ동시환자수)로 정의. 절대값이 아니라 기울기라 단일 스냅샷으로 검증 불가 → `presence-only`(기울기 산출 근거 기재).
  - 즉 값-검증 필드는 `instrumentation` 하나, 나머지 둘은 **분리 공식 + presence** 계약이다. 세 필드가 다 존재하지 않으면 FAIL.
- **성공기준 A4-c (stateless 금지 — bool 계약)**: `model.stateless_claim == False`(양 모델). XGB도 lookback 버퍼라는 per-patient 상태를 가지므로 하니스는 이 필드를 하드-`False`로 계약한다(리포트 산문 grep이 아니라 필드값 assert).

## A5. 공정성 통제

- **성공기준 A5-a (같은 스트림 + 무sleep = no-op)**: 두 모델에 **동일한 `.psv` 소스·동일 순서**로 요청을 흘린다. throughput 측정 시 inter-arrival sleep은 **no-op sleep_fn 주입**(호출은 되지만 실제 대기 0)으로 제거한다 — 리플레이 엔진이 행마다 `sleep_fn(interval)`을 호출하고 interval은 0이 될 수 없으므로(§B5), "무sleep"은 speed 조작이 아니라 no-op 함수 주입으로만 달성한다.
- **성공기준 A5-b (순차 실행)**: 두 서빙을 **동시에 띄우지 않고 순차** 측정한다(자원 경합 배제).
- **성공기준 A5-c (정상상태 워밍업 — 결정론적 컷)**: 부팅 비용(모델 로드 + GRU의 경우 드리프트 캘리브레이션)을 정상상태 latency와 **분리**한다. 컷은 두 단계로 **결정론화**(같은 latency 배열 → 같은 컷):
  1. **캘리브레이션 1회성 제외**: 첫 요청(index 0)은 무조건 워밍업으로 제외한다 — GRU 캘리브레이션은 lazy-load상 첫 요청에서만 1회 발생(§B5)하므로 index 0 제외로 대칭 흡수(XGB는 index 0에 모델 로드 tail).
  2. **정상상태 시작**: 수렴 판정의 driving 배열은 **client 벽시계 계열**로 고정한다(m-R4-1 — client·server 둘 다 요청별 계열이나 컷은 한 배열로 결정해야 결정론적; server latency ⊂ client 벽시계라 워밍업 스파이크가 양쪽 동일 index에 나타나 단일 `steady_state_start`를 양쪽에 적용). index 1부터, 연속 K=20 요청 창의 p95가 **직전 창 p95의 ±15% 이내**(경계 포함 = `|Δ| <= 0.15`, m-R3-3)로 든 **첫 창의 시작 index**를 `steady_state_start`로 고정, 그 이후만 정상상태 집계에 포함. (K·경계%·`<=`는 하니스 상수로 노출 — 값은 조정 가능하나 계약상 존재·결정성이 핵심. 경계 포함/배제를 명시해 정확히 15%인 배열에서 컷 index가 뒤집히지 않게 한다.)
  3. **비수렴 폴백 (M-R2-1, 결정론)**: run의 총 요청 수 안에서 ±15% 이내 창이 **하나도 없으면** `steady_state_start == −1`로 고정하고 해당 모델 run은 **명시적 FAIL**(집계 산출 금지 — 노이즈 궤적을 정상상태로 오인해 배달하지 않기 위함). 폴백은 "임의 컷 후 진행"이 아니라 FAIL이므로, 같은 비수렴 배열은 항상 `steady_state_start=−1`+FAIL로 결정론적 산출(spec-writer가 비수렴 입력→알려진 산출로 RED 고정 가능). CPU 노이즈로 인한 비수렴이 잦으면 K·% 상수를 조정하되, 조정 없이 "적당히 자르고 진행"하는 경로는 없다.

  워밍업/부팅 비용은 `boot_latency` 별도 항목으로 두 모델 대칭 기재. **정상상태 집계는 client·server latency 둘 다 동일한 `[steady_state_start:T]` 슬라이스**로 계산한다 — `client_mean`은 client 벽시계 배열의, `server_mean`은 server latency 요청별 계열(주입 계약 (ii))의 같은 슬라이스 평균. 둘 중 하나라도 index<`steady_state_start` 요청이 섞이면(예: server를 프로세스 수명 누적 `_sum/_count`로 산출) FAIL — 집합이 어긋나면 잔차 페어링(A2)이 깨지기 때문(B-R3-1). `steady_state_start`·`boot_latency`는 `ModelBench` presence 필드로 노출(m-R2-1) — spec-writer가 컷 index·부팅비용을 필드로 assert.

## A6. featureset 목표 = 결합 배포 프로파일 + 통제 arm (필수 게이트)

- **성공기준 A6-a (헤드라인 라벨 — enum)**: `BenchResult.headline_label == "combined_deployment_profile"` 그리고 `!= "pure_architecture"`. 헤드라인은 각 모델 실제 배포 featureset(GRU/vitals9, XGB/vitals_labs18)의 "(아키텍처 × featureset) 결합 배포 프로파일"이며, `"pure_architecture"`(금지값)면 FAIL.
- **성공기준 A6-b (통제 arm 필수 — presence)**: `BenchResult.control_arm.gru9`·`.xgb9`가 **둘 다 존재**한다(동일 featureset vitals9 통제 arm). 통제 arm 없이 배포 arm만 있으면(`control_arm is None` 또는 한쪽 결측) FAIL.
- **성공기준 A6-c (귀인 구조화 — 값 검증 vs presence 정직 구분)**: `BenchResult.attribution`이 비어있지 않고(지표당 `Attribution` 1개 이상), 각 항목이 두 기여로 분해된다.
  - **`featureset_contrib` (값 검증)**: 동일 아키텍처 내 9→18 delta로만 실측 — 예 `attribution[metric="memory.rss"].featureset_contrib == xgb.memory.rss − control_arm.xgb9.memory.rss`(XGB 안에서 featureset만 변함, 깨끗). 이 불변식으로 spec-writer가 RED 고정.
  - **`arch_contrib` (presence-only)**: 동일 featureset(vitals9) 아키텍처차 = `control_arm.gru9.M` vs `control_arm.xgb9.M`. 단 이 축엔 per-patient state 형태차(GRU hidden state vs XGB lookback 버퍼, NB2)가 섞이므로 단일 값으로 아키텍처 순수 기여를 **검증할 수 없다** → presence + 산출 근거 기재만(A4-b(2) 교차-아키텍처 금지와 정합).
  - 즉 A6-c는 산문이 아니라 `attribution` 필드 assert다 — 값 검증은 `featureset_contrib` 불변식, `arch_contrib`는 presence. 두 필드 다 없으면 FAIL.

## A7. 비용 — 구조화 산출 (손 표 아님)

- **성공기준 A7 (환산 불변식)**: `BenchResult.cost`가 아래를 만족한다.
  - `cost.instance_count == ceil(cost.target_throughput / cost.per_instance_throughput)`
  - `cost.cost_per_hr == cost.instance_count × cost.price_per_hr`
  - `cost.instance_type`·`cost.price_source`가 **비어있지 않다**(출처 명시·재현성).
  - `per_instance_throughput`은 측정(A3) 기반이어야 하며, CPU로 충분한지/GPU 필요한지 판단이 이 값에 반영된다. 손 표는 이 구조체의 렌더링일 뿐 TDD 대상은 `cost` 필드다(m-3).

## A8. 범위 밖

- 관측성 env 게이트 **구현** — handoff_2a(본 문서는 그 스위치를 **운영**만).
- 서빙 예측/추론 로직 변경 — 하지 않음.
- 자동 비용 대시보드·리포트 — 2차 이후(수동 표로 시작).
- 트랜스포머 벤치(가중치 없음).
- 크로스-featureset 스트림 주의: 각 모델은 **자기 featureset의 full psv**를 흘린다(GRU vitals9를 XGB vitals_labs18 서버에 보내면 9키 결측→NaN 퇴화 입력). 벤치 스트림 구성 시 이를 지킨다.

## A9. 선행 의존 (전제 — spec-writer 판단 아님)

- **라이브 arm-2 값 채우기(end-to-end GREEN)**는 handoff_2a의 관측성 env 게이트(arm-2 부가계측 OFF 토글, `SEPSIS_SERVE_AUX_METRICS`)에 **선행 의존**한다. 게이트 미구현 시 하니스는 주입 입력으로 집계·귀인 로직만 GREEN(A 입력 주입 계약), 라이브 값은 게이트 통과 후 채운다. **이것이 handoff_2a에 대한 유일한 선행 의존이다** — 버킷 관련 선행 의존은 없다(B-R2-1).
- **load-bearing 지표는 버킷 정밀도에 무관 (B-R2-1)**: A2 정직성 논증의 `residual`·`tax`는 `client_mean − server_mean`(`_sum/_count`, **버킷 무관 정확값**)이라 히스토그램 버킷 해상도와 독립이다. 따라서 A1-a의 load-bearing 평균·A2 residual/tax는 **어느 핸드오프의 버킷 설정에도 의존하지 않는다.** (라운드2의 fine-버킷 handoff_2a 거짓 귀속은 삭제 — fine 버킷은 어느 핸드오프도 소유하지 않는 고아였고, 평균 기반 재정의로 그 의존 자체가 사라졌다.)
- **서버 분위수는 분포 참고값 (버킷 의존은 여기에만, load-bearing 아님)**: `arm.server`(p50/p95/p99)는 분포 리포트용이다. 서버 히스토그램이 기본 버킷이면 sub-25ms CPU predict가 격자로 스냅된다(구현 근거는 §B2). 이는 **A1-b sanity 체크의 관용 폭을 넓힐 뿐**(EPS로 흡수) 정직성 논증(A2)에 영향 없다. 서버 분위수를 sub-25ms에서 정밀하게 보고 싶으면 fine 버킷이 필요하나, 이는 **load-bearing 요구가 아닌 분포 리포트 품질 개선**이며 별도 백로그 사안이다(어느 핸드오프에도 선행조건으로 걸지 않는다).

---

# §B. main 전용 — 구현 참조

> **spec-writer는 이 절을 읽지 않는다.** 경로·라인은 설계부 v5에서 `[확인됨]`으로 검증된 것.

## B1. 리플레이어 재사용 (예측 로직 불변)

- 요청 전송·타이밍 래핑: `src/sepsis/replay/http_sender.py`의 `HttpSender.send`(POST 후 dict 반환) 바깥에 벽시계 래핑을 추가. 예측 로직 불변.
- 동시 부하: `src/sepsis/replay/orchestrator.py`의 `replay_many`(ThreadPoolExecutor로 동시 스트림) 재사용 → A3 throughput 부하 생성기. **throughput 소스는 유일 patient_id N개**로 구성한다 — `orchestrator.py:45-52`가 중복 patient_id를 시작 전 `ValueError`로 막는다(F-c1, 서버 hidden state 오염 방지). N 스트림은 `PsvRowSource(run_suffix=...)` 등으로 pid를 유일화(A3 `unique_patient_ids == n_streams` 불변식). [확인됨]
- 스트림 무상태: `engine.py`의 `replay_stream`(환자 상태 미보유) 재사용. 리플레이 엔진은 행마다 `sleep_fn(interval)`을 호출(`engine.py:62-64`)하고 `interval = 3600/speed`는 0이 될 수 없으므로, "무sleep"(A5-a)은 speed 조작이 아니라 **no-op `sleep_fn` 주입**(호출은 되나 실제 대기 0)으로 달성. [확인됨]
- 추가되는 것은 (a) 벽시계 계측, (b) 벤치 러너(집계·리포트), (c) `BenchResult` 구조체 조립뿐. 스트리밍/시퀀스 로직 불변. `BenchResult`(§A0 스키마)를 최종 반환하고, 마크다운 리포트(§B7)는 그 구조체를 렌더링만 한다(TDD 대상 = 구조체).

## B2. 서버 히스토그램 수집

- `/metrics`에서 `serve_predict_latency_seconds`(Histogram)의 `_count`/`_sum`/`_bucket`을 스크레이프. **latency arm은 단일 스트림 순차**(A5-b)이므로, **요청마다 `_sum`을 스크레이프해 인접 델타**(`_sum_i − _sum_{i-1}`)를 취하면 그 요청의 서버 내부 latency가 복원된다 → **server latency 요청별 계열**을 만든다(client 벽시계 배열과 동일 길이·동일 요청집합). **load-bearing `arm.server_mean`은 이 계열의 `[steady_state_start:T]` 슬라이스 평균**으로 산출한다 — `client_mean`과 같은 정상상태 집합이라야 잔차 페어링이 성립(B-R3-1). **단일 누적 `_sum/_count`(프로세스 수명 전체) 사용 금지** — warmup·index 0이 섞여 client 슬라이스와 집합이 어긋나고 GRU 첫-predict 워밍업이 비대칭 오염을 낳는다. (두 시점 스냅샷 델타(warmup 경계·종료)는 **대안이 아니다** (M-R4-1): `steady_state_start`는 궤적 수렴으로 **사후 결정**되므로 수집 시점에 경계를 몰라 소급 슬라이스할 per-index 정보가 없다 — 정상상태 슬라이싱엔 요청별 `_sum` 계열이 필수다.) **분포 참고 분위수**(`_bucket` 보간)로 `arm.server`(p50/p95/p99)를 산출(A1-b sanity·리포트용). GRU·XGB 서빙 양쪽이 동일 이름 히스토그램을 노출(handoff_2a 1차 명세).
- **버킷은 load-bearing이 아님 (B-R2-1)**: `residual = client_mean − server_mean`은 `_sum/_count` 평균이라 버킷과 무관·정확하다 — A2 정직성 논증은 버킷 정밀도에 의존하지 않는다. 현재 `metrics.py:18`은 `LATENCY = Histogram("serve_predict_latency_seconds", ...)`로 **버킷 미지정** → prometheus 기본 버킷(.005/.01/.025/…)이라 `arm.server` **분위수**가 sub-25ms에서 격자 스냅되지만, 이는 **분포 리포트 품질**만 낮출 뿐 load-bearing 평균·residual/tax엔 영향 없다. A1-b `EPS`(분위수 sanity)는 최소 유효 버킷 폭으로 잡아 격자 스냅을 흡수. fine 버킷은 분포 리포트 개선용 별도 백로그이지 A9 선행조건이 아니다(라운드2 고아 의존 제거). [확인됨: `metrics.py:18` 버킷 미지정]
- 관측 경계는 각 서빙이 코드로 보장(GRU predict의 StreamPreprocessor 포함 / XGB 버퍼 재구성+booster.predict 포함 — 1차 handoff §B5).

## B3. arm-1/arm-2 운영

- arm-1: handoff_2a의 env 스위치를 **ON**(기본)으로 서버 기동 → 배포 계측 프로파일 측정.
- arm-2: 같은 스위치 **OFF**로 서버 재기동 → 순수 추론 프로파일 측정.
- 순차 실행(A5-b)이므로 arm 전환 = 서버 재기동으로 충분.
- 잔차 계산: `residual = client_mean − server_mean` (**버킷 무관 평균 기반**, B-R2-1). `client_mean`·`server_mean`은 각각 client 벽시계 계열·server latency 요청별 계열(§B2 인접 델타)의 **동일 `[steady_state_start:T]` 슬라이스 평균**(B-R3-1). 동일 요청집합이라 `mean(client) − mean(server) = mean(client − server)`로 per-request 페어링 성립 → 라운드1 unpaired proxy(m-1)·라운드2 fine 버킷 의존(B-R2-1) 동시 해소, arm1−arm2 tax 차분도 정확. `residual_label` enum 고정: arm-1 = `"client_server_residual"`(A2-a, `"network"` 금지), arm-2 = `"network_plus_serialization"`(A2-b), `tax = arm1.residual − arm2.residual`(A2-c), `residual == client_mean − server_mean`(A2-a2 불변식). 분위수(`client.pX`/`server.pX`)는 분포 리포트·A1-b sanity에만 쓰고 residual/tax엔 안 씀.

## B4. 메모리 측정

- 서빙 프로세스 RSS/peak: `/proc/<pid>/status`(VmRSS/VmHWM) 또는 컨테이너 stat. 순차 측정이라 각 서버 단독 RSS.
- 3기여 분해(A4-b) — 값 검증 vs presence 구분:
  - `memory.instrumentation`(값 검증) = **같은 모델의 arm-1 RSS − arm-2 RSS** 실측(계측 토글만 다름).
  - `memory.input_dim`(presence + 공식) = **동일 아키텍처 내 featureset delta**로만 — `control_arm.xgb9.memory.rss`(XGB/9) − 배포 `xgb.memory.rss`(XGB/18). `control_arm.gru9`/`.xgb9`는 `ModelBench`라 `.memory.rss`로 접근(m-R2-2). GRU/9 vs XGB/9로 재면 state 형태차가 섞이므로 교차-아키텍처 분리 금지(NB2).
  - `memory.state`(presence + 공식) = **동일 featureset·환자 수 sweep의 RSS 기울기**(ΔRSS/Δ동시환자수). 절대 스냅샷 아님 → presence-only. state 축은 GRU hidden state vs XGB lookback 버퍼 — 둘 다 환자 수 증가(`config.py` LOOKBACK=8; predictor hidden state).

## B5. 공정성 통제 구현

- 같은 psv 소스·순서: 동일 입력 파일·동일 seed·정렬. 무압축 발사는 **no-op `sleep_fn` 주입**(`replay_stream(..., sleep_fn=noop)`) — speed로는 interval을 0으로 못 만든다(`engine.py:55-56`이 speed>0 강제, interval=3600/speed>0). [확인됨]
- 정상상태 워밍업(A5-c): latency 궤적에서 (1) index 0 무조건 제외(캘리브레이션 1회성), (2) K=20 창 p95가 직전 창 ±15% 이내 든 첫 창 시작 index를 `steady_state_start`로 결정론적 컷. GRU 부팅 = 모델 로드 + `synthetic.calibrate(n_trials=300)`(`app.py:73` lazy-load 첫 요청 시 1회), XGB 부팅 = 모델 로드만 — `boot_latency` 항목 대칭 기재. [확인됨]

## B6. featureset arm 구성

- 배포 arm: GRU=vitals(9), XGB=vitals_labs(18). 통제 arm: 둘 다 vitals(9). XGB/9는 `xgboost_vitals.ubj`(1차 handoff B1 아티팩트), XGB/18은 `xgboost_vitals_labs.ubj`. 각 arm에서 해당 featureset full psv 스트림(A8).
- **GRU는 배포=통제 동일 featureset(둘 다 vitals9)** 이라 `control_arm.gru9`는 배포 `gru`의 별칭/재기재다(m-R2-3) — 별도 arm을 새로 돌릴 필요 없이 배포 gru 측정치를 gru9로 재기재해도 무방(featureset delta가 0). featureset 축 분리(A6-c `featureset_contrib`)가 XGB(9→18)에서만 실측되는 이유가 이것.

## B7. 리포트 산출물 (구조체의 하위 렌더링)

- **TDD 대상은 `BenchResult` 구조체(§A0)**, `docs/reports/serving_benchmark.md`는 그 구조체를 표·산문으로 **렌더링**한 것일 뿐(성공기준은 구조체 필드 assert, 리포트 substring 아님).
- 비용은 `BenchResult.cost` 구조체(A7 불변식: `instance_count=ceil(target/per_instance)`, `cost_per_hr=count×price`)를 표로 렌더 — 손 표 계산이 아니라 구조체 산출을 표기.
- 결합 프로파일을 발견으로 서술하되 per-patient state/입력차원/계측 3기여 분해 후 아키텍처 기여를 말함. 한계 명시(2모델 한정·통제 arm 범위·**서버 분위수는 default 버킷서 sub-25ms 격자 스냅되는 분포 참고값**(load-bearing residual/tax는 버킷 무관 평균이라 무영향, B-R2-1)·network는 arm-2 잔차로만 추정·비용 가정 출처). 라운드1 unpaired 분위수차 proxy(m-1)는 평균 기반 residual로 해소되어 더는 한계 아님.

---

## 핸드오프 검토 요청 항목 (redteam이 팔 자리)

1. §A의 리포트-기반 성공기준(A2·A4·A6)이 spec-writer에게 **관측/검증 가능**한가 — "리포트에 X 라벨이 있다/없다", "값이 차분으로 계산됐다"를 RED 테스트로 쓸 수 있는가(리포트 스키마를 §A가 충분히 고정했나). 애매하면 리포트 필드를 더 조여야.
2. A1-b(client ≥ server)가 항상 참인가 — 계측 오버헤드·시계 해상도로 위반될 엣지가 있나(eps 필요?).
3. A2 잔차 라벨링이 arm-1/arm-2를 실제로 가르는가 — handoff_2a 게이트에 의존하는데, 게이트 미구현 시 이 핸드오프가 GREEN 될 수 있나(의존 순서 명시 필요?).
4. A4-b 3기여 분해의 (1)(2)가 실제로 분리 측정 가능한가 — 통제 arm·환자 수 변화로 state와 입력차원 기여를 정말 가를 수 있나, 아니면 교란되나.
5. A5-c 정상상태 워밍업 컷이 두 부팅 프로파일(GRU 캘리브레이션 有 vs XGB 無)에 공정한가.
6. A6-b 통제 arm 필수 게이트가 "featureset 기여만" 분리한다는 주장이 NB2(통제 arm 잔차에 state 차 섞임)와 모순 없이 서술됐나.
7. A7 비용 모델 가정(인스턴스 타입·요금)이 재현 가능하게 명시되는가.
