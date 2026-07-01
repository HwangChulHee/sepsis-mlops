# Serving-Benchmark 핸드오프 검토 (2B — 벤치 하니스·실험 프로토콜·비용)

- **대상**: `docs/design/serving-benchmark/handoff_2b.md` (명세부 v1)
- **선행**: decisions.md v5 결정 3·4·5. 관측성 게이트(arm-2 토글)는 handoff_2a 소관.
- **핵심 질문**: §A 성공기준이 블랙박스 TDD RED 번역 가능 / 출제자(§A)-응시자(§B) 분리 / §B 코드 주장 사실성.

---

## 라운드 1

- 대상 commit: 작업트리 (초안)
- 검토일: 2026-07-02
- 판정: **HOLD — blocker 2건** (major 4, minor 3)

### PASS

- **§B 서버 히스토그램 관측 경계 = predict()만** — `app.py:96-98`이 latency 고정, `metrics.py:46`이 그 고정값만 관측, per-feature 루프·`get_window().add`는 이후. A2 잔차 근거 코드 일치. [확인됨]
- **§B5 부팅 캘리브레이션 비대칭 실재** — GRU `state()→_load_all`(`app.py:71-73,49-66`)이 `synthetic.calibrate(n_trials=300)`, XGB는 드리프트 스택 없음. A5-c/B5 비대칭 사실. [확인됨]
- **§B1 리플레이어 재사용 지점 실재** — `HttpSender.send`(`http_sender.py:25-32`), `replay_many`(`orchestrator.py:56` ThreadPoolExecutor), `replay_stream`(`engine.py:37-68` 무상태·speed). [확인됨]
- **§A에 src 라인 리터럴 없음** — §A는 `serve_predict_latency_seconds`·`/metrics`·`/predict` 등 관측 인터페이스 이름만. 형식상 분리 준수(검증 가능성은 blocker 참조).

### blocker

#### B-1. §A 성공기준 다수가 "손으로 쓰는 마크다운 리포트" 산문을 대상으로 해 TDD RED로 고정 불가
- **문제**: 최종 산출물이 `docs/reports/serving_benchmark.md`라는 **사람 서술 마크다운**(§B7, `handoff_2b:107-109`)인데, A1-a·A2-a·A2-c·A4-a/b/c·A6-a/b/c가 "리포트에 라벨 있다/없다", "값이 차분으로 계산"으로 서술. **테스트 대상 구조화 결과 객체(dataclass/dict/JSON)의 필드 스키마를 어디에도 고정 안 함.** 결과:
  - **A2-a("arm-1 리포트에 network 헤드라인이면 FAIL")를 기계 검사 불가** — substring `"network"`는 거짓양성(올바른 리포트도 A2-b/A2-c로 "network" 단어 포함). "arm-1의 network"와 "arm-2의 network"를 산문에서 결정론적으로 못 가름.
  - **A4-c("stateless 서술 없음")·A6-a("순수 아키텍처 운영비 표현 FAIL")**도 산문 substring이라 손으로 쓰는 md엔 작성 시점 타깃 자체가 없음.
  - **A2-c·A4-b(3) "값이 (arm-1−arm-2) 차분"** — 어느 필드가 arm-1/arm-2 잔차인지 §A에 없음.
- **근거**: `handoff_2b:16,22,29,31,42-43,53-54,107-109`. spec-writer는 §A만 읽고 RED 작성(`:4`)하는데 대상이 프로그램 산출 구조체가 아니라 산문이면 RED→GREEN 불가.
- **제안**: 하니스가 생성하는 **구조화 결과 객체**(`BenchResult` dataclass/JSON)를 §A에 명시, 각 성공기준을 명명 필드 assert로. 예: `arm1/arm2` = `{client:{p50,p95,p99}, server:{p50,p95,p99}, residual, residual_label}`; `residual_label`은 arm-1에서 열거형(`"client_server_residual"`), `"network"`는 **금지값**(enum 동등성 → A2-a 결정론화); `tax == arm1.residual − arm2.residual`; `A4-b {state,input_dim,instrumentation}` + `instrumentation == rss_arm1 − rss_arm2`; `A4-c stateless_claim: bool == False`; `A6-b control_arm` 필드 유무. 마크다운은 구조체의 하위 렌더링, TDD 대상은 구조체로 한정을 §A가 명시.

> **[reviser 응답]** 해소: §A에 **A0 `BenchResult` 스키마 절**을 신설(handoff_2b:20-85) — `ModelBench{arm1,arm2,tax,throughput,memory,stateless_claim}`, `ArmLatency{client,server,server_mean,residual,residual_label}`, `MemoryBreakdown{rss,peak,instrumentation,state,input_dim}`, `ControlArm{gru9,xgb9}`, `CostResult` 명명 필드로 고정. enum 고정값 표(handoff_2b:76-85) 추가: `residual_label` arm-1=`"client_server_residual"`(금지값 `"network"`), arm-2=`"network_plus_serialization"`; `headline_label`=`"combined_deployment_profile"`(금지값 `"pure_architecture"`). 각 성공기준을 필드 assert로 재작성 — A2-a는 **enum 동등성**(`== "client_server_residual"` & `!= "network"`, substring 아님, :96), A2-c는 불변식 `tax == arm1.residual − arm2.residual`(:98), A4-b(3) `instrumentation == rss_arm1 − rss_arm2`(:109-113), A4-c `stateless_claim == False`(:114), A6-a enum(:128), A6-b `control_arm.gru9/.xgb9` presence(:129), A7 비용 환산 불변식(:134-138). §A 인트로(:16-18)와 §B7(:196-200)에 "TDD 대상 = 구조체, 마크다운은 하위 렌더링" 명시. §A/§B 분리 유지: 스키마 필드명은 관측 계약이라 §A, 구현 함수·직렬화 포맷은 §B로 미룸.

#### B-2. A1 서버 분위수가 기본 버킷에서 sub-25ms에 해상도 부족 + A1-b(client≥server) eps 없어 flaky
- **문제**: `LATENCY = Histogram("serve_predict_latency_seconds", ...)`는 버킷 미지정 → prometheus **기본 버킷**(.005/.01/.025/…)(`metrics.py:18`). CPU predict()는 수ms~수십ms라 `.005/.01/.025`에 몰려 `histogram_quantile` 보간 p50/p95/p99가 5~25ms 격자로 스냅 → A1-a 서버 분위수 근사 무의미. A1-b는 **eps 없는 부등호**인데 client(벽시계 페어링 집계)와 server(버킷보간 분위수)는 **다른 추정량**이라 버킷 경계 근처서 server가 client보다 위로 튀어 **거짓 FAIL(flaky)**.
- **근거**: `metrics.py:18`(버킷 미지정), `handoff_2b:22-23`(A1-a/b), `:84`(§B2 버킷 정밀도 미언급). A1 서버 분위수 정밀도가 2a 버킷 설정 존재에 의존하는데 어디에도 없음.
- **제안**: (1) A1-b에 eps 허용오차 + 서버 분위수가 버킷보간 추정량임을 §A 명시(또는 sanity 경고로 강등). (2) sub-25ms 유의미하게 재려면 fine 버킷 요구를 §A 요구사항으로, 그 버킷이 handoff_2a에서 양쪽 설정돼야 A1 성립을 선행조건으로. 대안: 분위수 대신 `_sum/_count` 평균(버킷 무관) 병기.

> **[reviser 응답]** 해소: (1) A1-b를 `client.pX ≥ server.pX − EPS`로 재작성하고 "server는 버킷보간 추정량 / client는 벽시계 페어링 추정량 — 추정량 종류가 달라 버킷 경계서 근소 초과 가능, EPS 이내 초과는 sanity로 강등"을 §A에 명시(handoff_2b:90). (2) 스키마에 `server_mean`(`_sum/_count`, 버킷 무관 평균) 필드 추가 + A1-a에 병기 요구(:88) — 분위수가 격자 스냅돼도 평균은 유효(A1-a :89). (3) **A9 선행 의존 절** 신설(:148-151): "handoff_2a가 sub-25ms fine 버킷을 GRU·XGB 양쪽에 설정해야 A1 서버 분위수·EPS 유의미, 미충족 시 A1은 server_mean으로만 성립·분위수 sanity 강등"을 선행조건으로 못박음. §B2(:169)에 현재 `metrics.py:18` 버킷 미지정 사실과 2a 버킷 의존을 [확인됨]으로 기재.

### major

- **M-1. A5-c 워밍업 컷 "안정 범위로 수렴"이 비결정적 → RED 고정 불가.** 캘리브레이션 비용은 lazy-load 상 첫 요청 1회성(`app.py:73`)인데 A5-c가 결정론적 1회성 부팅과 통계 수렴 컷을 뒤섞음. **제안**: 컷을 수치로 — "첫 M요청 제외" 또는 "직전 중앙값 X% 이내 첫 요청부터 정상상태"를 §A에 고정. (`:49,101`)

> **[reviser 응답]** 해소: A5-c를 2단계 결정론 컷으로 재작성(handoff_2b:120-124) — (1) index 0 무조건 제외(캘리브레이션 lazy-load 1회성 흡수, 대칭), (2) K=20 창 p95가 직전 창 ±15% 이내 든 첫 창 시작 index를 `steady_state_start`로 고정, 이후만 정상상태 집계. "같은 latency 배열 → 같은 컷" 결정성 명시. K·%는 하니스 상수로 노출. 부팅비용은 `boot_latency` 별도 대칭 기재. §B5(:190)에 app.py:73 캘리브레이션 1회성 반영 [확인됨].
- **M-2. arm-2가 handoff_2a 게이트 의존인데 §A가 spec-writer에게 "주입값으로 테스트(라이브 게이트 구동 금지)"를 미지시 → 순서 의존/GREEN 불가.** §B3은 의존 명시하나 spec-writer는 §B 안 읽음. 현재 GRU 경로 토글 없음 확인(`app.py:98,102` 무조건, `SERVE_PER_PATIENT_GAUGE`는 gauge만). **제안**: §A에 "arm-1/arm-2는 주입된 알려진 입력으로 취급, spec-writer는 집계·귀인 로직 검증(라이브 게이트 구동 아님)" 명시 + 라이브 arm-2는 2a 선행을 순서 의존으로. [확인됨]

> **[reviser 응답]** 해소: §A 인트로에 **입력 주입 계약** 추가(handoff_2b:18) — "spec-writer는 라이브 서버·arm-2 토글 구동하지 않음; 원시 측정치(client 배열/metrics 스냅샷/proc RSS)는 주입 입력, 집계·귀인·라벨링 로직만 검증". A9 선행 의존(:150)에 "라이브 arm-2 end-to-end GREEN은 handoff_2a 게이트 선행"을 전제로 못박음(§B 안 읽는 spec-writer도 §A만으로 순서 의존 인지).
- **M-3. A3 throughput이 `replay_many` 중복 patient_id 금지(F-c1) 선행조건 미surface → ValueError.** `orchestrator.py:45-52`가 중복 pid면 시작 전 ValueError. N 스트림을 같은 환자로 복제하면 즉시 실패. **제안**: §B1에 "throughput 소스는 유일 patient_id(run_suffix 등)" 명시, A3의 "N 설정 가능"이 유일 pid N개 생성 함의를 못박기. (`orchestrator.py:45-52`, `:37,78`)

> **[reviser 응답]** 해소: A3에 불변식 `throughput.unique_patient_ids == throughput.n_streams` 추가 + "동시 스트림 = 유일 pid N개, 중복 pid는 서버 hidden state 섞어 시작 전 ValueError" 명시(handoff_2b:104). §B1(:162)에 "throughput 소스는 유일 pid N개, `orchestrator.py:45-52`가 중복 pid ValueError, `PsvRowSource(run_suffix=...)`로 유일화" 반영 [확인됨: orchestrator.py:45-52 코드 직접 확인].
- **M-4. A4-b (1)state·(2)입력차원 분리에 계산 공식 없어 presence-only + 모델 간 교란.** (3)계측만 `arm1−arm2` 공식, (1)(2)는 "갈라 기록"뿐. (2)입력차원을 GRU/9 vs XGB/9로 재려면 state 형태차 섞임(NB2) → (2)는 XGB 내부(9→18)에서만 깨끗. **제안**: (2)="동일 아키텍처 내 featureset delta(XGB/9→XGB/18 RSS차)로만 실측 분리" 공식, (1)="동일 featureset·환자 수 sweep RSS 기울기", 값 검증 불가 필드는 presence-only임을 정직 표시. (`:42,55`, NB2)

> **[reviser 응답]** 해소: A4-b를 "값 검증 vs presence 정직 구분"으로 재작성(handoff_2b:109-113) — (3) `instrumentation` = `rss_arm1 − rss_arm2` **값 검증**; (2) `input_dim` = **동일 아키텍처 내 featureset delta**(`control_arm.xgb9` RSS − 배포 `xgb`/18 RSS)로만, 교차-아키텍처(GRU/9 vs XGB/9) 분리 금지(NB2 state 형태차) → presence-only; (1) `state` = 동일 featureset·환자수 sweep RSS 기울기(ΔRSS/Δ환자수) → 단일 스냅샷 검증 불가라 presence-only. "값-검증은 instrumentation 하나, 나머지 둘은 분리공식+presence" 정직 표시. §B4(:182-186)에 동일 공식 반영.

### minor

- **m-1. 잔차 = 분위수 차는 unpaired proxy** — §B3 "client_p50 − server_p50"(`:91`)은 per-request 페어링 아님(히스토그램 per-request 미보유) → (arm1−arm2) 차분 음수/노이즈 가능. "분위수 차 proxy, 페어링 잔차 아님" caveat 명시. (`:31,91`)

> **[reviser 응답]** 해소: 스키마 `ArmLatency.residual` 주석(:44)과 A2 본문(handoff_2b:94)에 "unpaired 분위수차 proxy — 페어링 잔차 아님, arm1−arm2 노이즈 음수 가능" caveat. §B3(:177)·§B7(:200) 한계에 동일 caveat 병기.

- **m-2. A5-a 무sleep 경로 애매** — `engine.replay_stream`은 행마다 `sleep_fn(interval)` 호출(`engine.py:63-64`), interval=3600/speed는 0 불가. "무sleep"은 no-op `sleep_fn` 주입으로만. §B5 "speed 최대/무sleep"(`:100`)을 "no-op sleep_fn 주입"으로 구체화. 

> **[reviser 응답]** 해소: A5-a를 "무sleep = no-op sleep_fn 주입(호출은 되나 대기 0), speed 조작 불가(interval=3600/speed>0)"로 구체화(handoff_2b:118). §B5(:189)에 `replay_stream(..., sleep_fn=noop)` + `engine.py:55-56` speed>0 강제 [확인됨].

- **m-3. A7 비용표 손 표라 TDD 대상 모호** — 비용 **환산 함수**(`instance_count=ceil(target/per_instance)×price`)를 구조화 산출로, 표는 렌더링 분리하면 B-1과 함께 A7도 테스트 가능. (`:59,65`)

> **[reviser 응답]** 해소: B-1 스키마에 `CostResult{target_throughput, per_instance_throughput, instance_count, price_per_hr, cost_per_hr, instance_type, price_source}` 흡수(handoff_2b:68-75). A7을 환산 불변식으로 재작성 — `instance_count == ceil(target/per_instance)`, `cost_per_hr == count × price`, `instance_type/price_source` 비어있지 않음(:134-138). 표는 구조체 렌더링(§B7:198).

### 판정

**blocker 2건 → HOLD.** B-1(리포트 스키마 미고정 → 산문 substring 비검증)이 핵심 — 테스트 대상을 손으로 쓰는 md에서 **명명 필드 구조화 결과 객체**로 옮겨야 A2-a/A4-c/A6-a가 결정론 RED 가능. B-2(히스토그램 버킷 해상도·A1-b eps)도 실측 유효성에 load-bearing.
