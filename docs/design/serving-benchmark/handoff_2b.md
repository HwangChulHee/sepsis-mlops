# Serving-Benchmark 구현 핸드오프 (명세부) — 2B: 벤치 하니스·실험 프로토콜·비용

> **전제**: `docs/design/serving-benchmark/decisions.md`(설계부) v5, 5라운드 검토 통과(blocker 0). 본 문서는 그 **결정 3(계측점·지표)·결정 4(공정성 통제·arm 운영·featureset 귀인)·결정 5(비용)**를 명세한다. 관측성 env 게이트(arm-2 토글)는 **handoff_2a**로 분리 — 본 문서는 그 스위치가 "존재한다"를 전제로 arm-1/arm-2를 **운영**한다.
> **워크플로우**: 검토(`handoff_2b_review.md`) 통과 → spec-writer가 §A만 보고 TDD(RED) → main이 §A+§B로 구현(GREEN). 푸시는 사람 게이트(자동 금지).
> **출제자-응시자 분리**: §A(계약·성공기준·실패모드)는 **spec-writer 전용** — src 라인 참조 없이 관측 가능한 행동으로만. §B(구현 참조)는 **main 전용**.
> **상태**: 명세부 v1 — 레드팀 검토 전.

## 0. 한 줄 요약

같은 환자 스트림을 GRU·XGB 서빙에 순차로 흘려 **latency·throughput·메모리**를 수집하고, 인스턴스 요금으로 환산해 **(아키텍처 × featureset) 실배포 운영비 프로파일**을 만든다. 리플레이어(`src/sepsis/replay/`)를 벤치 하니스로 확장한다(예측 로직 불변, 계측만 추가). 정직성 3원칙: (1) latency는 **client 벽시계 + 서버 히스토그램** 둘 다 재고, 그 차(잔차)를 arm-1에서 "network"라 부르지 않는다. (2) 메모리 차이는 **per-patient state·입력차원·계측** 3기여로 갈라 귀인한다. (3) 헤드라인은 순수 아키텍처가 아니라 **결합 배포 프로파일**이며, featureset 기여는 **통제 arm(GRU/9 vs XGB/9)**으로 분리한다.

---

# §A. spec-writer 전용 — 계약·성공기준·실패모드

> spec-writer는 이 절만 읽고 TDD를 작성한다. 벤치 하니스의 **관측 가능한 산출**(수집 함수의 입출력, 리포트 아티팩트의 구조·라벨·계산값)으로 검증한다. 서버를 실제로 띄우는 통합 시나리오는 **알려진 입력→알려진 산출**로 성공기준을 고정한다.

## A1. latency 수집 — 두 계측점 병행

리플레이어가 각 `/predict` 요청을 보낼 때 **client 벽시계**(요청 직전~응답 직후)를 잰다. 동시에 서버가 노출하는 **서버 내부 latency 히스토그램**(`serve_predict_latency_seconds`, `/metrics`)도 수집한다.

- **성공기준 A1-a (두 값 다 있음)**: 벤치 리포트에 각 모델별로 **client 벽시계 latency**의 p50/p95/p99와 **서버 히스토그램 latency**의 p50/p95/p99가 **둘 다** 기록된다. 하나만 있으면 FAIL.
- **성공기준 A1-b (client ≥ server)**: 같은 요청 집합에서 client 벽시계 latency는 서버 히스토그램 latency보다 **크거나 같다**(client가 network+직렬화+핸들러 후처리를 포함하므로). 위반 시 계측 오류.

## A2. 잔차 정직성 — arm에 따라 다르게 라벨링 (load-bearing)

`client 벽시계 − 서버 히스토그램`을 **"client−server 잔차"**라 부른다. 이 잔차의 라벨링은 arm에 따라 다르다:

- **성공기준 A2-a (arm-1에서 network 금지)**: 부가 계측을 **켠 상태(arm-1)**로 측정한 리포트는 이 잔차를 **"network"라고 이름 붙이지 않는다** — "client−server 잔차(network+직렬화+핸들러 후처리)"로만 표기한다. arm-1 리포트에서 "network latency = X"라는 헤드라인이 나오면 FAIL.
- **성공기준 A2-b (network 추정은 arm-2에서만)**: 부가 계측을 **끈 상태(arm-2)**의 잔차만 "network+직렬화 추정"으로 헤드라인할 수 있다.
- **성공기준 A2-c (핸들러 후처리분 = 차분)**: 리포트는 **(arm-1 잔차 − arm-2 잔차)**를 "부가 계측 세금"으로 별도 표기한다 — network에 흡수하지 않는다.

> **근거**: 서버 히스토그램은 순수 추론 구간만 재고, 핸들러의 부가작업(피처 루프·드리프트 적재)은 잔차에 섞인다. 이 부가작업은 GRU 9 vs XGB 18로 **비대칭**이라, arm-1 잔차를 통째로 network라 부르면 XGB network가 체계적으로 부풀려진다.

## A3. throughput — 동시 부하

- **성공기준 A3**: 벤치는 **동시 N 스트림**(N을 설정 가능)으로 부하를 걸어 초당 처리 요청 수(또는 완료 시간)를 각 모델별로 리포트에 기록한다. 단일 스트림 latency와 별개 항목이다.

## A4. 메모리 — RSS/peak + 3기여 분해

- **성공기준 A4-a (측정 존재)**: 각 서빙 프로세스의 **RSS와 peak**를 리포트에 기록한다.
- **성공기준 A4-b (3기여 분해)**: 메모리 차이를 하나의 숫자로 뭉뚱그리지 않고 **세 기여로 갈라** 기록한다 — (1) per-patient 상태(GRU hidden state vs XGB lookback 버퍼, **둘 다** 동시 환자 수에 증가), (2) 입력차원(9 vs 18), (3) 계측 부속물(드리프트 윈도우 + 피처 히스토그램). 특히 (3)은 **arm-1 RSS − arm-2 RSS**의 차분으로 실측 분리한다.
- **성공기준 A4-c (stateless 금지)**: 리포트 어디에도 "XGB는 stateless"라는 서술이 없다 — XGB도 lookback 버퍼라는 per-patient 상태를 가진다.

## A5. 공정성 통제

- **성공기준 A5-a (같은 스트림)**: 두 모델에 **동일한 `.psv` 소스·동일 순서**로 요청을 흘린다. inter-arrival sleep 없이(무압축 발사) throughput을 왜곡하지 않는다.
- **성공기준 A5-b (순차 실행)**: 두 서빙을 **동시에 띄우지 않고 순차** 측정한다(자원 경합 배제).
- **성공기준 A5-c (정상상태 워밍업)**: 부팅 비용(모델 로드 + GRU의 경우 드리프트 캘리브레이션)을 정상상태 latency와 **분리**한다 — 워밍업 컷은 "연속 K요청의 p95가 안정 범위로 수렴한 시점"까지 실측 제외하고, 부팅 비용은 **별도 항목**으로 리포트(두 모델 부팅 프로파일 대칭 기재). 정상상태 집계에 부팅 비용이 섞이면 FAIL.

## A6. featureset 목표 = 결합 배포 프로파일 + 통제 arm (필수 게이트)

- **성공기준 A6-a (헤드라인 라벨)**: 헤드라인 프로파일은 **각 모델의 실제 배포 featureset**(GRU/vitals9, XGB/vitals_labs18)이며, 리포트는 이를 "(아키텍처 × featureset) 결합 배포 프로파일"로 명시한다. "순수 아키텍처 운영비"라는 표현을 쓰면 FAIL.
- **성공기준 A6-b (통제 arm 필수)**: 배포 arm(GRU/9 vs XGB/18)뿐 아니라 **동일 featureset 통제 arm(GRU/9 vs XGB/9)**을 **반드시 함께** 측정·기록한다. 통제 arm 없이 배포 arm 숫자만 있는 리포트는 **FAIL**.
- **성공기준 A6-c (귀인 명시)**: 모든 지표에 대해 "이 차이 중 featureset(9→18) 기여 vs 아키텍처 기여"를 분해 기재한다. 단 통제 arm 잔차엔 per-patient state 차이(hidden state vs lookback 버퍼)가 섞임을 명시한다(통제 arm은 featureset만 고정).

## A7. 비용표

- **성공기준 A7**: 목표 throughput(예: 병동 N환자/시간)을 치는 데 필요한 **인스턴스 대수 × $/hr**를 수동 표로 환산한다. 인스턴스 타입·요금 **출처를 명시**하고 재현 가능해야 한다. CPU로 충분한지/GPU 필요한지도 측정 결과 기반으로 표에 기재.

## A8. 범위 밖

- 관측성 env 게이트 **구현** — handoff_2a(본 문서는 그 스위치를 **운영**만).
- 서빙 예측/추론 로직 변경 — 하지 않음.
- 자동 비용 대시보드·리포트 — 2차 이후(수동 표로 시작).
- 트랜스포머 벤치(가중치 없음).
- 크로스-featureset 스트림 주의: 각 모델은 **자기 featureset의 full psv**를 흘린다(GRU vitals9를 XGB vitals_labs18 서버에 보내면 9키 결측→NaN 퇴화 입력). 벤치 스트림 구성 시 이를 지킨다.

---

# §B. main 전용 — 구현 참조

> **spec-writer는 이 절을 읽지 않는다.** 경로·라인은 설계부 v5에서 `[확인됨]`으로 검증된 것.

## B1. 리플레이어 재사용 (예측 로직 불변)

- 요청 전송·타이밍 래핑: `src/sepsis/replay/http_sender.py`의 `HttpSender.send`(POST 후 dict 반환) 바깥에 벽시계 래핑을 추가. 예측 로직 불변.
- 동시 부하: `src/sepsis/replay/orchestrator.py`의 `replay_many`(ThreadPoolExecutor로 동시 스트림) 재사용 → A3 throughput 부하 생성기.
- 스트림 무상태: `engine.py`의 `replay_stream`(환자 상태 미보유) 재사용. `speed` 파라미터는 벤치 시 무압축(sleep 최소, A5-a).
- 추가되는 것은 (a) 벽시계 계측, (b) 벤치 러너(집계·리포트)뿐. 스트리밍/시퀀스 로직 불변.

## B2. 서버 히스토그램 수집

- `/metrics`에서 `serve_predict_latency_seconds`(Histogram)의 `_count`/`_sum`/`_bucket`을 스크레이프해 p50/p95/p99 산출(prometheus 히스토그램 분위수). GRU·XGB 서빙 양쪽이 동일 이름 히스토그램을 노출(handoff_2a 1차 명세). 관측 경계는 각 서빙이 코드로 보장(GRU predict의 StreamPreprocessor 포함 / XGB 버퍼 재구성+booster.predict 포함 — 1차 handoff §B5).

## B3. arm-1/arm-2 운영

- arm-1: handoff_2a의 env 스위치를 **ON**(기본)으로 서버 기동 → 배포 계측 프로파일 측정.
- arm-2: 같은 스위치 **OFF**로 서버 재기동 → 순수 추론 프로파일 측정.
- 순차 실행(A5-b)이므로 arm 전환 = 서버 재기동으로 충분.
- 잔차 계산: `client_p50 − server_p50` 등. arm-1 잔차엔 "network" 라벨 금지(A2-a), arm-2 잔차만 network 추정(A2-b), (arm-1−arm-2) 잔차 = 계측 세금(A2-c).

## B4. 메모리 측정

- 서빙 프로세스 RSS/peak: `/proc/<pid>/status`(VmRSS/VmHWM) 또는 컨테이너 stat. 순차 측정이라 각 서버 단독 RSS.
- 3기여 분해(A4-b): (3) 계측 부속물 = arm-1 RSS − arm-2 RSS 실측. (1) per-patient state·(2) 입력차원은 동시 환자 수·featureset을 바꿔가며 관측(예: 통제 arm과 배포 arm의 XGB RSS 차 = featureset 기여). state 축은 GRU hidden state vs XGB lookback 버퍼 — 둘 다 환자 수 증가(`config.py` LOOKBACK=8; predictor hidden state).

## B5. 공정성 통제 구현

- 같은 psv 소스·순서: 동일 입력 파일·동일 seed·정렬. 무압축 발사(speed 최대/무sleep).
- 정상상태 워밍업(A5-c): 부팅 요청 latency 궤적을 기록해 p95 수렴점을 실측 컷. GRU 부팅 = 모델 로드 + `synthetic.calibrate(n_trials=300)`(app.py 부팅 시), XGB 부팅 = 모델 로드만 — 부팅 항목 대칭 기재.

## B6. featureset arm 구성

- 배포 arm: GRU=vitals(9), XGB=vitals_labs(18). 통제 arm: 둘 다 vitals(9). XGB/9는 `xgboost_vitals.ubj`(1차 handoff B1 아티팩트), XGB/18은 `xgboost_vitals_labs.ubj`. 각 arm에서 해당 featureset full psv 스트림(A8).

## B7. 리포트 산출물

- `docs/reports/serving_benchmark.md`에 결과. 결합 프로파일을 발견으로 서술하되 per-patient state/입력차원/계측 3기여 분해 후 아키텍처 기여를 말함. 한계 명시(2모델 한정·통제 arm 범위·network는 arm-2 잔차로만 추정·비용 가정 출처).

---

## 핸드오프 검토 요청 항목 (redteam이 팔 자리)

1. §A의 리포트-기반 성공기준(A2·A4·A6)이 spec-writer에게 **관측/검증 가능**한가 — "리포트에 X 라벨이 있다/없다", "값이 차분으로 계산됐다"를 RED 테스트로 쓸 수 있는가(리포트 스키마를 §A가 충분히 고정했나). 애매하면 리포트 필드를 더 조여야.
2. A1-b(client ≥ server)가 항상 참인가 — 계측 오버헤드·시계 해상도로 위반될 엣지가 있나(eps 필요?).
3. A2 잔차 라벨링이 arm-1/arm-2를 실제로 가르는가 — handoff_2a 게이트에 의존하는데, 게이트 미구현 시 이 핸드오프가 GREEN 될 수 있나(의존 순서 명시 필요?).
4. A4-b 3기여 분해의 (1)(2)가 실제로 분리 측정 가능한가 — 통제 arm·환자 수 변화로 state와 입력차원 기여를 정말 가를 수 있나, 아니면 교란되나.
5. A5-c 정상상태 워밍업 컷이 두 부팅 프로파일(GRU 캘리브레이션 有 vs XGB 無)에 공정한가.
6. A6-b 통제 arm 필수 게이트가 "featureset 기여만" 분리한다는 주장이 NB2(통제 arm 잔차에 state 차 섞임)와 모순 없이 서술됐나.
7. A7 비용 모델 가정(인스턴스 타입·요금)이 재현 가능하게 명시되는가.
