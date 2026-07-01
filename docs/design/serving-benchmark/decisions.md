# Serving-Benchmark 설계결정문서 (DDD) — 아키텍처별 운영비용 벤치마크

> **설계 근거**: 운영 엔지니어 포지셔닝 자산 — "모델러는 정확도만, 운영 엔지니어는 정확도 대비 운영비를 본다." H2에서 학습한 아키텍처(GRU·XGBoost)를 **서빙했을 때의 지연/처리량/메모리/비용**을 공정 측정·비교한다. 리플레이어(`src/sepsis/replay/`) 인프라를 벤치 하니스로 확장한다.
> **워크플로우·출처등급**: `CLAUDE.md`. 검토(`docs/design/serving-benchmark/review.md`) 통과 후 핸드오프로.
> **상태**: 설계부 v3 — 라운드 2 blocker 3건 보완, 재검토 대기.
> **개정 이력**
> - v1: 초안 (설계부).
> - v2: 라운드 1 — B1(서버 latency 히스토그램 존재 전제 수정·network 분해), B2(계측 대칭성 arm 분리·메모리 귀인), B3(featureset 목표 (A) 확정·통제 arm 게이트), M1/M2/m1/m2 반영.
> - v3: 라운드 2 — NB1(network 분해식이 히스토그램 관측 경계와 어긋남 → 분해를 arm-2 한정 + 잔차 정직 재명명), NB2(XGB "stateless" 폐기 → 환자별 8행 lookback 버퍼 상태 명문화·메모리 대비 재서술), NB3(arm-2 부가계측 토글 소재 확정 → 관측성 전용 게이트를 결정 1 격리 예외로 명문화), MJ1(XGB 아티팩트 소스·preprocess.json 경로 식별), mn1(분해식 명칭 정직화) 반영.

## 한 줄 요약

같은 환자 스트림을 **각 아키텍처의 독립 서빙**에 흘려 **latency·throughput·메모리**를 측정하고, 인스턴스 요금으로 환산해 **"정확도 대비 운영비" 프로파일**을 만든다. 첫 바퀴는 **GRU/vitals vs XGBoost/vitals_labs 두 모델**, **3지표 + 수동 비용표**. 핵심 스코프 결정: **서빙을 다형화(멀티모델 어댑터)하지 않는다** — 각 모델을 *따로* 최소 서빙으로 띄워 *순차* 벤치한다(런타임 교체 불필요, 측정만 목적). 리플레이어의 `HttpSender`·`replay_many`를 재사용하되 예측/추론 로직은 불변.

## 범위 / 범위 외

| 범위 (serving-benchmark) | 범위 외 |
|---|---|
| GRU·XGBoost 각각의 **독립 최소 서빙** 기동 | 통합 멀티모델 서빙 어댑터(런타임 종류 교체) |
| 리플레이어 확장: **요청별 latency 계측**(client 벽시계 + 서버 내부 히스토그램 병행) | 서빙 예측/추론 *로직* 변경 (환자 안전 분리) |
| **동시 부하 throughput** 측정(`replay_many` 재사용) | 트랜스포머 벤치(가중치 없음 — 2차 바퀴) |
| 서빙 프로세스 **메모리(RSS/peak)** 측정 | 자동 비용 리포트·대시보드(수동 표로 시작) |
| **수동 비용 환산표**(인스턴스 $/hr × 필요 대수) | 콘솔·champion-challenger 통합(무관) |
| 공정성 통제(같은 스트림·하드웨어·계측점·워밍업) | 자동 스케일링·오토파일럿 최적화 |

> **스코프 핵심(결정 2)**: "서빙이 GRU 전용"이라는 기존 한계를 *다형화로 풀지 않는다*. 벤치는 런타임 교체가 필요 없으므로, 각 모델용 최소 서빙을 각각 띄워 각각 측정한다. 통합 어댑터는 명시적 범위 외(YAGNI).

---

## 결정 1: 트리거 철학 — 측정 하니스지 운영 통합이 아님

- **결정**: 이 작업의 산출물은 **벤치마크 하니스 + 비용 리포트**지, 프로덕션 기능이 아니다. 콘솔·서빙 프로덕션 경로의 **예측/추론 로직**을 건드리지 않는다. 리플레이어를 측정 도구로 확장하고, 결과는 리포트(`docs/reports/serving_benchmark.md`)로 남긴다.
- **★격리 예외 = 관측성 전용 게이트 (NB3)**: 결정 4 arm-2(순수 추론 프로파일)를 재사용 GRU 경로에서 돌리려면 부가 계측(`metrics.record`의 per-feature 루프 + `get_window().add`)을 **끌 수 있어야** 하는데 현재 GRU `app.py`엔 이 토글이 없다 [확인됨: `app.py:98,102` 무조건 호출, `metrics.py:52-56` 무조건 루프; 유일한 env 토글 `SERVE_PER_PATIENT_GAUGE`는 gauge만 가드 `metrics.py:38-40`]. 따라서 **env-게이트 관측성 스위치 1개**를 추가한다 — 단 이 스위치가 가드하는 것은 **관측성 부속작업(per-feature INPUT 히스토그램 루프 + drift window add)에 한정**하고, **예측/추론 로직(`predict()`·`_row_from`·응답 dict·`serve_predict_latency_seconds` LATENCY 관측)은 불변**이다. 즉 격리 원칙은 "프로덕션 경로 절대 불변"이 아니라 **"예측/추론 로직 불변, 관측성은 env-게이트로 가감 가능"**으로 정밀화한다. 이는 코드베이스에 **이미 존재하는 패턴**(`SERVE_PER_PATIENT_GAUGE` 옵트인 gauge 가드, `metrics.py:38-40`)의 확장이지 새 아키텍처가 아니다 [확인됨: 기존 옵트인 게이트 패턴]. (게이트가 가드하는 정확한 경계·기본값은 핸드오프에서 확정 — 설계부는 "무엇을 가드하고 무엇을 불변으로 두는지"까지.)
- **근거 + 출처등급**:
  - 리플레이어가 이미 "환자 .psv → /predict 스트리밍" + 동시 스트림(`replay_many`, ThreadPoolExecutor)을 함 [확인됨: `src/sepsis/replay/orchestrator.py`].
  - 운영비 비교는 운영 엔지니어 포지셔닝 자산(딥오토=플랫폼 비용, 동서=실배포 비용) [우리 결정].
  - 관측성 전용 게이트는 예측 결과를 바꾸지 않으므로 환자 안전 분리를 위반하지 않음 [우리 결정].
- **고려한 대안**: (i) 프로덕션 서빙에 멀티모델 통합 후 벤치(과함 — over-engineering, 결정 2에서 기각). (ii) arm-2용 **벤치 전용 GRU 최소 앱(계측 없는 사본)** — 결정 2 "GRU=기존 재사용"과 어긋나고, 프로덕션과 코드가 갈라져 "프로덕션 서빙을 실측한다"는 벤치 목적을 훼손(기각, NB3 옵션 b). (iii) arm-2를 범위 외로 낮추고 arm-1만 헤드라인 — 순수 추론 비용(B2 최대 교란원 통제)을 포기(기각, NB3 옵션 c). → **옵션 (a) 관측성 전용 게이트 채택**.
- **검토 요청 항목**: 관측성 게이트가 예측/추론 로직을 정말 안 건드리는지(grep으로 predict/응답 경로 불변 확인 가능한지).

---

## 결정 2: ★핵심 — 서빙 다형화 안 함, 모델별 독립 최소 서빙 + 순차 벤치

- **결정**: 기존 서빙(`src/sepsis/serve/app.py`)은 **GRU 전용**이다 [확인됨: app.py GRU 로드·hidden state·시퀀스 전제]. XGBoost를 벤치하려고 서빙을 다형화(predict 어댑터)하지 **않는다**. 대신:
  1. 각 모델용 **최소 서빙 엔드포인트**를 각각 기동(GRU 서빙 = 기존 재사용, XGB 서빙 = 동일 `/predict` 계약을 따르는 최소 앱).
  2. **동시에 안 띄우고 순차** 측정(자원 경합 배제 — 결정 4).
  3. 두 서빙은 **같은 `/predict` 요청/응답 전체 스키마**를 지킨다 (M1) → 리플레이어가 양쪽에 동일하게 흐른다. 요청 = `{patient_id: str, features: dict[str, float|None]}` (absent/null → NaN, 0-fill 금지), 응답 = `{patient_id, p, alarm, featureset}` **네 키 전부** [확인됨: `app.py:77-79,103-104`]. XGB 최소 앱은 응답 키를 부분이 아니라 **전체 복제**한다(리플레이어/집계기가 응답 dict를 동일 파싱).
- **★XGB 서빙은 stateless가 아니다 — 환자별 lookback 버퍼 상태 필요 (NB2)**: XGB 챔피언 입력은 단일 timestep raw가 아니라 **8시간 lookback 요약통계**다 [확인됨: `config.py:61-62` `LOOKBACK=8`·`TREE_STATS` 7종; `data/features.py:30-59` `lookback_summary`가 `(T,F)→(T,F*7)`, row t = 윈도우 `[t-7..t]` 요약; `train/tree.py:3` "Input = per-timestep lookback summaries"]. 즉 vitals9 → 63차원, vitals_labs18 → 126차원 입력을 매 timestep 만들어야 채점 가능하다. 따라서:
  1. **의존 명시(설계부 수준)**: XGB 최소 서빙은 **환자별 최근 8행 raw 버퍼**를 유지해, 매 `/predict` 요청마다 버퍼 + 신규 행으로 `data/features.lookback_summary`를 호출해 63/126차원 입력을 구성한다. 이 버퍼는 **동시 환자 수에 따라 증가하는 per-patient 상태** — GRU hidden state에만 귀속시켰던 바로 그 특성을 XGB도 (형태만 다르게) 갖는다. (버퍼 자료구조·소멸 정책·직렬화 등 구현은 핸드오프.)
  2. **`/predict` 계약은 유지**: 요청은 GRU와 동일하게 **단일 timestep raw features 1행**이다(계약 불변). 63차원 요약은 서버가 **자기 버퍼로 재구성**한다(클라이언트가 8행을 보내지 않는다) — GRU가 요청 1행을 받아 서버측 hidden state로 시퀀스를 잇는 것과 대칭. 버퍼가 아직 8행 미만이면 사용 가능한 행으로만 요약(윈도우 패딩은 학습과 동일하게 NaN-aware, `features._windows`가 앞을 NaN 패드 `features.py:25`).
  3. **train-serve skew 경계 명시**: 버퍼 없이 1행만으로 요약하면 `mean=min=max=last`, `delta/var` 퇴화 → 학습분포와 어긋나는 skew. 이를 막으려 버퍼 유지가 **선택이 아니라 필수**임을 명문화한다 [우리 결정].
- **★XGB 아티팩트·전처리 소스 식별 (MJ1)**: XGB 최소 앱은 승격 별칭이 아니라 **MLflow run 디렉토리**에서 로드한다 — `deploy/artifacts/`엔 GRU 별칭만 있고 XGB 승격 별칭은 없다 [확인됨: `deploy/artifacts/`에 `gru_vitals*`만, xgb 없음]. 소스 = `mlruns/1/3e21f380b380422d8d52f78904e54ad4/artifacts/model/xgboost_vitals.ubj`(9) · `mlruns/1/fe64aac54f344999baa217f56e4e963c/artifacts/model/xgboost_vitals_labs.ubj`(18), 각 run의 `artifacts/preprocess.json`(keys: `featureset, scale_pos_weight, tau, hp, note`)이 featureset과 **alarm 임계 `tau`**를 제공한다 [확인됨: 파일 실재·JSON keys]. XGB는 트리 NaN-native라 정규화(mean/std) 불필요 — preprocess.json에 정규화 통계 없음이 정상 [확인됨: keys에 mean/std 없음]. (run id 하드코딩 대신 벤치 설정으로 주입할지는 핸드오프.)
- **근거 + 출처등급**:
  - app.py가 GRU 종류에 묶임(hidden state 이어받기·시퀀스 흘리기) [확인됨: app.py].
  - 벤치는 런타임 종류 교체가 불필요 → 다형화는 안 쓸 유연성(YAGNI) [우리 결정].
  - 같은 계약이라야 리플레이어 `HttpSender.send`가 양쪽에 재사용됨 [확인됨: `http_sender.py` send()].
- **고려한 대안**: 서빙 다형화(어댑터 인터페이스) — 벤치 목적엔 과함, 범위 외. XGB를 HTTP 없이 오프라인 추론만 측정 — GRU의 HTTP 서빙 latency와 사과-오렌지(기각, 결정 4 계측 일관성 위배). XGB를 "1행=1요약"으로 단순화(버퍼 생략) — train-serve skew 유발(기각, 위 3).
- **미결/옵션**: XGB 최소 서빙을 새 앱으로 짤지, 기존 serve 모듈에 분기 플래그로 둘지는 핸드오프. 단 분기 플래그가 GRU **예측/추론 로직**을 오염시키면 안 됨(환자 안전 분리; 관측성 게이트는 결정 1 예외).
- **검토 요청 항목**: XGB 최소 서빙이 GRU 서빙과 "같은 계약·다른 종류"를 만족하는지, lookback 버퍼가 계약을 안 깨고 서버측 상태로만 성립하는지, 기존 서빙 예측 경로 오염 없는지.

---

## 결정 3: 지표 3종 + 계측점 = client 벽시계 **+ 서버 내부 히스토그램**(둘 다 수집)

- **결정**: 첫 바퀴 지표 = **(a) latency**(요청별, p50/p95/p99), **(b) throughput**(동시 N환자 부하 시 초당 처리 요청/시점 수), **(c) 메모리**(서빙 프로세스 RSS·peak). 비용은 결정 5에서 수동 환산.
- **계측점 = client 벽시계 + 서버 내부 히스토그램 병행**. 두 값을 **동시에** 수집한다:
  - **client 벽시계**: 리플레이어 `HttpSender.send`에 타이밍 래핑 [확인됨: `http_sender.py` send()가 POST 후 dict 반환 — 래핑이 최소 변경].
  - **서버 내부 추론 latency**: 기존 서빙이 **이미** 이를 실측·노출한다. `metrics.py:18`에 `LATENCY = Histogram("serve_predict_latency_seconds", ...)`가 정의돼 있고, `app.py:96-98`이 `t0=perf_counter(); out=pred.predict(...); metrics.record(perf_counter()-t0, ...)`로 관측한다 [확인됨: `metrics.py:18,46`, `app.py:96-98`].
- **★히스토그램 관측 경계 = `predict()`만, 핸들러 후처리는 밖 (NB1)**: `metrics.record`의 첫 인자 `perf_counter()-t0`는 `predict()` 반환 **직후 고정**되고 `metrics.py:46` `LATENCY.observe(latency_s)`가 그 고정값을 관측한다. per-feature INPUT 루프(`metrics.py:52-56`, GRU 9회/XGB 18회)와 `get_window().add`(`app.py:102`)는 `LATENCY.observe` **이후** 실행된다 [확인됨: `app.py:96-98,102`, `metrics.py:43-56` 실행 순서]. 따라서 히스토그램은 **순수 predict() 구간만** 재고, 핸들러 후처리(부가 계측)는 히스토그램 밖·client 벽시계 안에 있다.
- **★잔차 정직 재명명 + 분해식은 arm-2 한정 (NB1·mn1)**:
  - **client − 서버 히스토그램 = client−server 잔차 = (network + 직렬화 + 핸들러 후처리)**. 이 잔차를 **arm-1(부가계측 ON)에서 "network"로 헤드라인 출하하지 않는다** — 잔차 안엔 per-feature 루프(GRU 9 vs XGB 18, **비대칭**)와 window.add가 섞여 있어, network로 오귀인하면 XGB 쪽이 체계적으로 부풀려진다.
  - **network 추정치는 arm-2(부가계측 OFF)에서만 헤드라인**한다. arm-2에선 per-feature 루프·window.add가 게이트로 꺼져(결정 1 예외·결정 4 arm-2) 핸들러 후처리분이 사라지므로, `client − 히스토그램 ≈ network + 직렬화 + 프레임워크 serialize`로 좁혀진다 [우리 결정].
  - **핸들러 후처리분은 (arm-1 잔차 − arm-2 잔차)의 차분으로 별도 재분리**해 리포트한다 — network로 흡수하지 않고 "부가 계측 세금"으로 정직 귀인 [우리 결정].
- **XGB 최소 앱에도 동일 계측점 필수**: XGB 서빙도 `/predict` 내부에 **동일한 `serve_predict_latency_seconds` 히스토그램(같은 이름·같은 관측 위치 = predict 호출 감싸기만)**을 둔다 [우리 결정]. 그래야 두 서빙에서 (client 벽시계, 서버 히스토그램, 잔차 분해)가 **같은 관측 경계**로 비교된다.
- **근거 + 출처등급**:
  - 서버 히스토그램이 관측하는 것은 `predict()` 구간뿐이고 핸들러 후처리는 그 밖 [확인됨: `app.py:96-98,102`, `metrics.py:43-56`].
  - `replay_many`가 ThreadPoolExecutor로 동시 스트림 → throughput 부하 생성기로 재사용 [확인됨: `orchestrator.py`].
  - 메모리는 `/proc/<pid>/status`(RSS) 또는 컨테이너 stat로 peak 추적 [검증 필요: 측정 방법 핸드오프]. **단 무엇을 재는지(순수 추론 RSS vs 계측 부속물 포함)는 결정 4 B2가 먼저 확정 — 측정법은 그 뒤 정한다** (m2).
- **고려한 대안**: (i) client-side 벽시계 **전용**(서버 히스토그램 무시) — 서버가 이미 predict() latency를 노출하는데 안 쓰면 잔차 분해 수단을 버리는 것(기각). (ii) arm-1 잔차를 그대로 "network"라 부름 — 비대칭 후처리 오귀인(기각, NB1). (iii) 히스토그램 관측 경계를 핸들러 전체(부가작업 포함)로 옮김 — 프로덕션 `serve_predict_latency_seconds`의 의미(순수 추론)를 바꿔 관측 회귀 유발(기각; 대신 arm-2 게이트로 client 벽시계 쪽을 좁힘).
- **검토 요청 항목**: XGB 최소 앱의 `serve_predict_latency_seconds` 관측 위치가 GRU와 동일 경계(predict 호출만 감쌈)인지, arm-2에서 잔차가 실제로 network+직렬화로 좁혀지는지, 메모리 peak 측정법이 두 서빙에 공정한가.

---

## 결정 4: 공정성 통제 — 같은 스트림·같은 하드·순차·**정상상태 워밍업**·**계측 대칭성**

- **결정**: 비교가 공정하려면 아래를 고정한다.
  - **같은 환자 스트림**: 동일 `.psv` 소스·동일 순서. `speed`는 벤치 시 **무압축(즉시 발사, sleep 최소)** — inter-arrival sleep은 throughput 측정을 왜곡하므로 [확인됨: `replay_stream(speed=...)` 의미].
  - **같은 하드웨어**: 동일 머신/컨테이너. 두 서빙 **순차 실행**(동시 실행 시 CPU·메모리 경합으로 상호 오염 → 순차로 배제).
  - **정상상태 워밍업 (컷 = 부팅 비용 실측 후 제외)**: 첫 요청은 lazy-load뿐 아니라 **300-trial 드리프트 캘리브레이션**까지 유발한다 — `state()`가 `_load_all`을 부르고 이 안에서 `synthetic.calibrate(ref, n_trials=300)`가 돈다 [확인됨: `app.py:61-66,73`, `_load_all`]. 따라서 워밍업을 "모델 로드"만으로 정의하면 안 된다. **워밍업 컷은 latency가 정상상태(연속 K요청의 p95가 안정 범위)로 수렴한 시점까지를 실측해 버린다** [우리 결정]. GRU 부팅 비용(모델 로드 + 캘리브레이션)과 XGB 부팅 비용(모델 로드만 — 드리프트 스택 없음)은 **구조가 다르므로**, 부팅 비용은 정상상태 latency와 **분리해 별도 항목**으로 리포트(양쪽 부팅 프로파일 대칭 기재). 정상상태 집계에는 부팅 비용을 섞지 않는다 (M2).
  - **★계측 대칭성 (B2 — 최대 교란원 통제)**: 재사용되는 GRU `app.py`의 `/predict`는 추론 외에 **매 요청 부가작업**을 한다 — `metrics.record`가 피처 수만큼 루프 돌며 `INPUT_FEATURE/INPUT_MISSING`을 관측(GRU 9회, XGB면 18회)하고 [확인됨: `metrics.py:52-56`], `get_window().add`가 float32 행을 최대 5000개 deque에 적재한다 [확인됨: `app.py:102`, `window.py:26-34`]. XGB "최소 앱"이 이를 빼면 latency·메모리가 사과-오렌지가 된다. 따라서 벤치 arm을 **두 축으로 명시 분리**한다 [우리 결정]:
    - **arm-1 (배포 계측 프로파일)**: 두 서빙 모두 `metrics.record`(per-feature 루프) + `get_window().add`를 **동일하게 켠 채** 측정. XGB 최소 앱도 GRU와 **같은 계측 표면**(동일 `metrics.record` 호출 + 동일 drift window add)을 갖춘다 → 요청당 계측 세금이 대칭.
    - **arm-2 (순수 추론 프로파일)**: 두 서빙 모두 부가 계측(per-feature 히스토그램·drift window)을 **끄고**(추론 + 결정 3의 `serve_predict_latency_seconds` 관측만 남김) 측정 → 아키텍처 순수 추론 비용.
    - **★arm-2 토글 소재 = 관측성 전용 env 게이트 (NB3)**: GRU 경로엔 현재 이 토글이 없다 [확인됨: `app.py:98,102` 무조건, `SERVE_PER_PATIENT_GAUGE`는 gauge만 가드 `metrics.py:38-40`]. 결정 1의 **격리 예외**로 도입하는 env-게이트가 이 토글이다 — 게이트가 가드하는 것은 **per-feature INPUT 루프(`metrics.py:52-56`) + `get_window().add`(`app.py:102`)에 한정**, **`LATENCY`(`serve_predict_latency_seconds`) 관측·`predict()`·응답은 불변**. GRU/XGB 양쪽이 **같은 게이트 의미**를 공유한다(XGB 최소 앱도 동일 env로 부가계측 on/off). "끄고 측정"의 실체 = 이 게이트다(토글 소재 명시). 게이트 이름·기본값·정확한 가드 지점은 핸드오프.
    - **메모리 귀인 — XGB도 stateful (NB2 반영, 3기여 유지·축 재정의)**: RSS 리포트를 세 기여로 분해하되 "state 기여" 축을 **GRU 전용에서 양쪽으로** 고친다 [우리 결정]:
      - **(1) per-patient state**: GRU hidden state(환자당 고정크기 벡터, `predictor.py:41-51`) **vs XGB lookback 버퍼(환자당 8행 raw, `config.py:61` `LOOKBACK=8` / `features.lookback_summary` 입력용, 결정 2)**. **둘 다 동시 환자 수에 증가** — "stateful는 GRU만"이라는 v2 서술은 폐기. 두 상태의 형태·크기 차이를 재는 게 발견.
      - **(2) 입력차원**: 9 vs 18(featureset). XGB는 요약 후 63 vs 126차원으로 증폭됨도 기재.
      - **(3) 계측 부속물**: drift-window(최대 5000행) + Prometheus per-feature 히스토그램. arm-2(게이트 OFF)와 arm-1의 RSS 차분으로 이 기여분을 실측 분리.
      GRU RSS 차이를 "stateful 아키텍처"로 뭉뚱그리지 않는다 — 세 기여를 갈라 기재한 뒤에야 아키텍처 기여를 말한다 [우리 결정].
  - **전처리 경계 명시**: GRU는 ffill+평균, XGB는 요약통계 전처리가 `/predict` 내부에 있음. 측정은 **end-to-end(전처리 포함)** — 운영자가 실제 지불하는 비용이므로. 단 리포트에 "전처리 포함"을 명시.
- **근거 + 출처등급**:
  - 자원 경합·콜드스타트·**계측 비대칭**이 벤치 오염원 [확인됨: app.py lazy-load + 300-trial 캘리브레이션 / `metrics.py:52-56` per-feature 루프 / `window.py` deque].
  - 전처리 포함이 "실제 운영비"에 정직 [우리 결정].
- **★featureset 목표 확정 (B3 — 옵션 (A)로 확정, 미결 종료)**: GRU 챔피언=vitals(9), XGB 챔피언=vitals_labs(18)로 최적 featureset이 다르다 [확인됨: `config.py:51-52` 9 vs 18]. **첫 바퀴 목표 = (A) "실제 배포 프로파일"로 확정한다.** 즉 헤드라인 숫자는 **각 모델이 실제로 배포되는 (아키텍처 × featureset) 결합 프로파일**이며, "순수 아키텍처 운영비"가 **아니다**. 이 확정의 필수 통제 3가지 [우리 결정]:
  1. **"아키텍처 비대칭" 단독 헤드라인 폐기**: 어떤 지표도 featureset 기여를 뗀 "아키텍처 운영비"로 제목화하지 않는다(부속 결정도 개정 — 아래).
  2. **모든 지표에 featureset 기여 명시 귀인**: latency·throughput·메모리 각각에 "이 차이 중 입력차원(9 vs 18) 기여 vs 아키텍처 기여"를 **분해 기재**한다.
  3. **귀인을 뒷받침할 통제 분해 arm(필수 PASS 게이트)**: 위 2의 귀인이 수사(修辭)가 되지 않도록, **두 아키텍처를 동일 featureset(vitals9)로도 함께 측정하는 통제 arm을 필수로 돌린다.** (배포 arm = GRU/9 vs XGB/18, 통제 arm = GRU/9 vs XGB/9.) 통제 arm과 배포 arm의 **XGB 쪽 차이**(XGB/9 → XGB/18)가 곧 featureset(9→18) 기여의 실측치다. 이 통제 arm 없이 배포 arm 숫자만 출하하는 것은 PASS 불가. **단 (NB2) 통제 arm은 featureset을 고정할 뿐, GRU/9 vs XGB/9의 잔차엔 여전히 per-patient state 차이(GRU hidden state vs XGB lookback 버퍼)가 섞인다** — 통제 arm이 분리하는 것은 "featureset 기여"이지 "순수 아키텍처의 단일 원인"이 아님을 리포트에 명시(state 기여는 메모리 3기여 분해가 따로 귄인).
  > 옵션 (B)("아키텍처 비교"를 헤드라인으로) 기각 이유: 문서 전체 포지셔닝이 "운영 엔지니어 = 정확도 대비 **실배포** 운영비"이므로 헤드라인은 실배포 프로파일이 정직하다. 단 (A)의 귀인 요구를 지키려 (B)의 통제 arm을 **분해 수단**으로 흡수했다(목표는 하나 = (A)).
- **고려한 대안**: 두 서빙 동시 실행(경합 오염 — 기각). 콜드스타트 포함(콜드스타트 성능이 목적이 아니면 오염 — 기각). 배포 arm만 돌리고 featureset 통제 생략(오귀인 숫자 출하 — 기각, B3).
- **검토 요청 항목**: arm-1/arm-2 계측 표면이 두 서빙에서 실제 대칭인지, 통제 분해 arm이 featureset 기여를 정말 분리하는지, 부팅 비용 분리가 두 부팅 프로파일에 공정한지.

---

## 결정 5: 비용 환산 — 수동 표(1차), 자동 리포트는 2차

- **결정**: latency·throughput을 **인스턴스 요금**으로 환산: "목표 throughput(예: 병동 N환자/시간)을 치려면 인스턴스 몇 대 × $/hr". CPU로 되는지/GPU 필요한지도 표에 [검증 필요: GRU·XGB 각 CPU 충분 여부는 측정 결과에 종속]. 첫 바퀴는 **수동 표**(스프레드시트/마크다운), 자동 리포트·대시보드는 2차.
- **근거 + 출처등급**:
  - 현재 학습 GRU 가중치는 CPU 환경 전제 [검증 필요: 1차 아티팩트로 재확인 — m1, "메모리"가 auto-memory인지 배포 아티팩트인지 출처 불명, 강등]. XGB는 경량 CPU. GPU 필요성은 측정으로 판정 [검증 필요].
  - YAGNI: 자동 리포트는 수치 안정화 후 [우리 결정].
- **고려한 대안**: 처음부터 자동 비용 대시보드(수치 미검증 상태에서 과투자 — 기각).
- **검토 요청 항목**: 비용 모델 가정(인스턴스 타입·요금 출처)이 명시·재현 가능한가.

---

## 부속 결정

- **결합 프로파일 = 발견이지 버그 아님 (B3 반영·NB2 개정)**: **둘 다 per-patient 상태를 가진다** — GRU는 환자별 hidden state(고정크기 벡터), **XGB는 환자별 8행 lookback 버퍼**(요약통계 입력 재구성용, 결정 2·`config.py:61` `LOOKBACK=8`) [확인됨: `features.py:30-59`, `config.py:61-62`]. **둘 다 동시 환자 수에 메모리 증가**하며, "XGB는 stateless"라는 v2 서술은 **폐기**한다(허위 전제). 상태의 **형태·크기 비대칭**(벡터 vs raw 윈도우)이 측정의 핵심 결과지만, 첫 바퀴 헤드라인은 결정 4대로 **(아키텍처 × featureset) 결합 배포 프로파일**이다 — "GRU가 메모리 더 먹네"를 **순수 아키텍처 특성으로 뭉뚱그리지 않는다.** 메모리 차이에는 (1) per-patient state(GRU hidden state vs XGB lookback 버퍼), (2) 입력차원(9 vs 18 → 요약 후 63 vs 126), (3) 계측 부속물(drift window·per-feature 히스토그램)이 섞이므로, 리포트는 결정 4의 통제 분해 arm(GRU/9 vs XGB/9)과 메모리 귀인 분리로 **세 기여를 갈라 기재**한 뒤에야 "아키텍처 기여"를 말한다.
- **리플레이어 예측 로직 불변**: 추가되는 것은 (a) `HttpSender`의 latency 계측, (b) 벤치 러너(집계·리포트)뿐. 스트리밍/시퀀스 로직 불변 [확인됨: engine `RowSource`/`Sender` 추상].
- **트랜스포머 = 2차 바퀴**: 학습 가중치 부재 → 첫 바퀴 제외. 3모델로 확장 시 결정 2(독립 서빙)·4(공정성)를 그대로 재적용.

## PASS 기준 (핸드오프에 박을 게이트 — 초안)

1. **예측/추론 로직 불변**: 프로덕션 서빙의 **예측/추론 로직**(`predict`·응답 dict·`_row_from`)·콘솔 미변경(grep 강제). **단 결정 1 격리 예외 — 관측성 전용 env 게이트(per-feature 루프·drift window add 가드, 예측/추론·`LATENCY` 관측 불변)는 허용**(NB3). XGB 벤치는 독립 최소 서빙으로, 기존 GRU **예측 경로** 오염 없음. XGB 최소 앱 응답은 `{patient_id, p, alarm, featureset}` **전체 스키마** 복제(M1). **XGB 아티팩트 소스 = `mlruns/1/…/model/xgboost_{vitals,vitals_labs}.ubj` + 각 run `preprocess.json`(tau)**, 벤치 설정으로 주입(MJ1).
2. **계측**: latency는 **client 벽시계(`HttpSender` 래핑) + 서버 히스토그램(`serve_predict_latency_seconds`, `predict()` 구간만)** 병행, p50/p95/p99. **client − 히스토그램 = client−server 잔차(= network + 직렬화 + 핸들러 후처리)** — 이 잔차를 arm-1에서 "network"로 헤드라인 금지(NB1·mn1). **network 추정치는 arm-2(부가계측 OFF)에서만 헤드라인**(잔차가 network+직렬화로 좁혀짐), 핸들러 후처리분은 (arm-1 잔차 − arm-2 잔차) 차분으로 별도 귀인. throughput은 `replay_many` 동시 부하. 메모리는 서빙 프로세스 RSS·peak. **XGB 최소 앱도 GRU와 동일 관측 경계**(`serve_predict_latency_seconds`, predict만 감쌈).
3. **공정성**: 같은 스트림·같은 하드·**순차 실행**·**정상상태 워밍업(부팅 비용 실측·분리)**·**전처리 포함 명시**. **계측 대칭성(B2)**: arm-1(배포 계측 켬, 두 서빙 대칭) + arm-2(순수 추론, 관측성 게이트로 부가계측 끔; 게이트 소재 = 결정 1 예외 env 스위치, NB3) 둘 다 측정, 메모리 RSS에서 drift-window·per-feature 히스토그램 기여분 분리 귀인.
4. **featureset 목표 = (A) 실제 배포 프로파일 (B3 확정)**: 헤드라인 = (아키텍처 × featureset) 결합 배포 프로파일, "순수 아키텍처 운영비" 표현 금지. **통제 분해 arm(GRU/9 vs XGB/9) 필수 실행 = PASS 게이트** — 없이 배포 arm 숫자만 출하 시 FAIL. 모든 지표에 featureset 기여 vs 아키텍처 기여 분해 귀인. **단 통제 arm 잔차엔 per-patient state 차이(GRU hidden state vs XGB lookback 버퍼)가 섞임을 명시**(NB2 — 통제 arm은 featureset만 고정).
5. **비용표**: 목표 throughput → 인스턴스 대수 × $/hr 수동 환산, 요금 가정·출처 명시·재현 가능.
6. **리포트**: 결과를 `docs/reports/serving_benchmark.md`에 — 결합 프로파일을 발견으로 서술하되 **per-patient state(GRU hidden state vs XGB lookback 버퍼)/입력차원/계측 3기여를 분해**한 뒤 아키텍처 기여를 말함(NB2 — "XGB stateless" 금지). 한계 명시(2모델 한정·통제 arm 범위·network는 arm-2 잔차로만 추정).
7. **범위 정직**: 트랜스포머·자동 리포트·멀티모델 어댑터가 범위 외로 명시.

---

## 검토 상태

- v1(본 문서): 레드팀 검토 전.
- v2(라운드 1 보완): redteam blocker 3건 해소 — B1(결정 3 서버 히스토그램 존재 전제 수정 + network 분해), B2(결정 4 계측 대칭성 arm-1/arm-2 + 메모리 귀인 분리), B3(결정 4 featureset 목표 = (A) 확정 + 통제 arm PASS 게이트). major M1(응답 전체 스키마)·M2(정상상태 워밍업·부팅 비용 분리), minor m1(메모리 CPU 출처 강등)·m2(측정 선후 명시) 반영.
- v3(라운드 2 보완): redteam blocker 3건 해소 — **NB1**(결정 3·PASS 게이트 2 — 히스토그램 관측 경계=`predict()`만임을 명시 `app.py:96-98,102`/`metrics.py:43-56`, 잔차를 "client−server 잔차(network+직렬화+핸들러 후처리)"로 재명명, network 분해식을 arm-2 한정으로 못박음, 핸들러 후처리분은 arm-1−arm-2 차분 귀인), **NB2**(결정 2·4·부속 결정·PASS 게이트 4/6 — "XGB stateless" 폐기, 환자별 8행 lookback 버퍼 상태 명문화 `config.py:61-62`/`features.py:30-59`/`tree.py:3`, 계약 유지하며 서버측 버퍼 재구성·train-serve skew 경계, 메모리 3기여의 state 축을 양쪽으로 재정의), **NB3**(결정 1·4·PASS 게이트 1/3 — arm-2 부가계측 토글 소재 = 관측성 전용 env 게이트, 결정 1 격리 예외로 명문화, 기존 `SERVE_PER_PATIENT_GAUGE` 옵트인 패턴 확장). major **MJ1**(결정 2·PASS 게이트 1 — XGB 아티팩트 소스 `mlruns/1/…/model/xgboost_{fs}.ubj` + `preprocess.json(tau)` 식별), minor **mn1**(잔차 명칭 정직화) 반영.
- 1차 확인: 리플레이어 `replay_many`·`HttpSender.send`·engine 추상 / app.py GRU 전제·lazy-load·`/metrics` 존재. **정정(v2): `serve_predict_latency_seconds` 히스토그램은 존재한다** [확인됨: `metrics.py:18,46`, `app.py:96-98`]. **정밀화(v3): 그 히스토그램은 `predict()` 구간만 관측하고 핸들러 후처리(per-feature 루프·window.add)는 밖** [확인됨: `app.py:96-98,102`·`metrics.py:43-56` 실행 순서]. **XGB는 lookback 요약 입력이라 서빙도 per-patient 상태 필요** [확인됨: `config.py:61-62`·`features.py:30-59`·`train/tree.py:3`]. **XGB 아티팩트는 `mlruns/1/…`에만, `deploy/artifacts/`엔 GRU 별칭만** [확인됨: 디렉토리 실측].
- 예상 blocker 후보(레드팀이 팔 자리): ①XGB lookback 버퍼가 계약을 안 깨고 서버측 상태로 성립하는지·초기(8행 미만) 요약의 skew ②arm-2 잔차가 실제로 network+직렬화로 좁혀지는지 ③관측성 게이트가 예측 로직 불변을 grep으로 증명 가능한지 ④통제 arm 잔차의 state 기여를 메모리 분해가 실제로 분리하는지 ⑤순차 실행이 하드웨어 상태(캐시·thermal) 차이를 남기는지.
- 다음: review-loop(redteam⇄reviser) → blocker 0 → 핸드오프 → TDD.