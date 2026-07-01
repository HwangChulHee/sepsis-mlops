# Serving-Benchmark DDD 레드팀 검토

- 대상: `docs/design/serving-benchmark/decisions.md`
- 핵심 질문: 결정 2 스코프 경계 정합 / 결정 4 featureset 비대칭 / client-side 계측 정직성 / [검증 필요]·[확인됨] 코드 대조

---

## 라운드 1

- 대상 commit: `320a8c0` (main)
- 검토일: 2026-07-01
- 판정: **HOLD — blocker 3건**

### PASS (이미 정합한 부분)

- **결정 2 경계 "app.py는 GRU 전용"** — 코드로 확인됨. `src/sepsis/serve/app.py:46` `_resolve_alias`가 `gru_{fs}` 별칭을 하드코딩, `predictor.py:41-51`이 `forward_state`+per-patient hidden state, `StreamPreprocessor`(ffill+mean)에 묶임. `src/sepsis/serve`에 xgb/xgboost 참조 0건. 서빙은 실제로 GRU 전용. PASS.
- **리플레이어 재사용 주장** — 정확. `HttpSender.send`(`http_sender.py:25-32`) POST 후 dict 반환, `replay_many`가 `ThreadPoolExecutor`로 동시 스트림(`orchestrator.py:56`), `replay_stream` 무상태(`engine.py:59`). "예측 로직 불변 + 계측만 추가"는 코드와 정합.
- **featureset 9/18 카운트** — 확인됨. `config.py:51-52` `FEATURESET_VITALS=9`, `FEATURESET_VITALS_LABS=18`.
- **누수 신설 없음** — 엔진이 행을 raw 전달(`engine.py:65`), 서버가 결측→NaN(`app.py:88`), 0-fill/정규화 없음. PASS.

### blocker

#### B1. 결정 3의 핵심 [확인됨] 전제가 코드와 정면 배치 — 서버 latency 히스토그램은 존재한다
- **문제**: 결정 3(`decisions.md:58`)은 "서빙 app.py에 latency 히스토그램이 **없음** … [확인됨: app.py에 Histogram/Summary 없음]"이라 단언하고 이를 client-side 전용 계측의 근거로 삼는다. 이 [확인됨]은 **거짓**.
- **근거**:
  - `src/sepsis/serve/metrics.py:18` — `LATENCY = Histogram("serve_predict_latency_seconds", "per-/predict latency")`
  - `metrics.py:46` — `LATENCY.observe(latency_s)`
  - `app.py:96-98` — `t0 = time.perf_counter(); out = s["pred"].predict(...); metrics.record(time.perf_counter() - t0, ...)` — 서버 내부 추론 latency를 실측해 `/metrics`로 노출.
- **왜 blocker**: (1) [확인됨] 태그가 사실과 반대. (2) 이 거짓 전제 위에 결정 3 전체(client-side 전용)와 "network 포함을 어떻게 정직화하나"(`:64`)가 서 있다. 서버가 이미 `serve_predict_latency_seconds`로 순수 내부 추론 시간을 노출하므로 **client 벽시계 − 서버 히스토그램 = network+직렬화**로 3번 우려(network vs 서버 내부 못 가름)를 실제 해소 가능. 문서는 이 수단의 존재조차 언급 안 하고 "못 가르니 한계로 명시"로 넘어간다.
- **제안**: 전제 수정 + GRU/XGB 양쪽 서빙에 `serve_predict_latency_seconds`(동일 계측점)를 두어 client 벽시계와 병행 수집. 최소한 결정 3에서 "서버 내부 latency 히스토그램이 이미 있고 이를 network 분해에 쓴다"를 명시.

> **[reviser 응답]** 해소: 결정 3을 재작성. 거짓 [확인됨: 히스토그램 부재]를 삭제하고, `serve_predict_latency_seconds`가 `metrics.py:18,46`·`app.py:96-98`에 실재함을 [확인됨]으로 정정(코드 직접 대조). 계측점을 "client 벽시계 + 서버 내부 히스토그램 병행"으로 바꾸고 **network+직렬화 = client − 서버 내부**를 분해식으로 결정에 박음(한계로 넘기지 않음). XGB 최소 앱에 동일 계측점(같은 히스토그램 이름·predict 감싸는 동일 위치) 이식을 필수로 명시. 검토 상태 §의 v1 "부재" 주장도 정정. (decisions.md 결정 3 전체 · 검토 상태 §, PASS 게이트 2)

#### B2. 공정성 통제에서 최대 교란원 누락 — GRU 서빙(전체 앱) vs XGB "최소 앱"의 계측 비대칭
- **문제**: 결정 2.1은 "GRU 서빙 = 기존 재사용", XGB = "최소 앱"으로 규정(`:42`). 그러나 재사용되는 `app.py`의 `/predict`는 추론 외에 **매 요청 계측/드리프트 부가작업**을 한다. XGB "최소 앱"이 이를 뺀다면 latency·메모리 비교가 사과-오렌지가 된다. 결정 4의 "공정성 통제"는 이 비대칭을 다루지 않는다.
- **근거**:
  - `app.py:98` `metrics.record(...)` → `metrics.py:52-56` **피처 수만큼 루프** 돌며 `INPUT_FEATURE.labels().observe()`/`INPUT_MISSING.labels().inc()` (GRU 9회, XGB라면 18회).
  - `app.py:102` `get_window().add(...)` → `window.py:26-34` 요청마다 float32 행 복사 후 최대 5000개 deque 적재.
  - `app.py:61-66` 부팅 시 `synthetic.calibrate(..., n_trials=300)` — 첫 요청이 모델 로드뿐 아니라 **300-trial 캘리브레이션**까지 유발(결정 4 `:73`은 "첫 predict 시 로드"만 언급).
- **왜 blocker**: (1) latency: GRU는 요청마다 metrics 루프+drift add 세금, XGB 최소 앱은 안 냄. (2) 메모리: 부속결정(`:97`)은 메모리 차이를 "stateful vs stateless **아키텍처 특성**"이라 규정하나, GRU RSS엔 5000행 drift 윈도우 + Prometheus per-feature 히스토그램(계측)이 섞임 — 아키텍처가 아니라 **계측 오염**. 핵심 산출물이 계측 비대칭과 분리 불가능해 해석 불능.
- **제안**: 두 서빙의 계측 표면을 **동일하게 맞추거나**(둘 다 metrics.record+drift window 포함/제외 대칭), 벤치 시 부가작업 비활성 "순수 추론 프로파일"을 별도 arm으로 두고 메모리 리포트에서 drift-window/히스토그램 기여분 분리 측정. 결정 4에 "계측 대칭성"을 공정성 통제 항목으로 추가.

> **[reviser 응답]** 해소: 결정 4에 "★계측 대칭성(B2)" 공정성 통제 항목을 신설. arm-1(배포 계측 프로파일 — 두 서빙 모두 `metrics.record` per-feature 루프 + `get_window().add`를 대칭으로 켬, XGB 최소 앱도 동일 계측 표면)과 arm-2(순수 추론 프로파일 — 부가 계측 끄고 추론 + `serve_predict_latency_seconds`만)로 **두 축 분리**. 메모리 RSS는 drift-window(최대 5000행)·per-feature 히스토그램 기여분을 아키텍처 기여와 갈라 분리 귀인하도록 명시. 부속 결정의 "GRU 메모리 = 아키텍처 특성" 뭉뚱그림도 개정(3기여 분해 후 서술). (decisions.md 결정 4 "★계측 대칭성" · 부속 결정 · PASS 게이트 3)

#### B3. 결정 4 featureset 비대칭 — 목표 진술의 내부 모순 + 게이트가 오귀인을 허용
- **문제**: 문서는 목표를 한편으로 "각 아키텍처의 독립 서빙"·"아키텍처 비대칭 = 발견"(`:11`, `:97`)이라는 순수 아키텍처 비교로 세우면서, 결정 4(`:78`)에서는 vitals(9) vs vitals_labs(18)로 **입력 차원이 2배 다른** "실제 배포 프로파일"(옵션 가)을 우선 제안한다. 양립 불가. PASS 게이트(`:105`)는 "featureset 처리 방식이 리포트에 명시"만 요구, 통제 arm도 지표별 귀인도 강제 안 함.
- **근거**: `config.py:51-52`(9 vs 18 확정). 옵션 (가)만 돌리면 latency·메모리 차이에 아키텍처 기여와 입력차원(2배) 기여가 섞이고, 독자는 헤드라인 숫자를 "아키텍처 운영비"로 오귀인. 문서 스스로 "(가)면 순수 아키텍처 비교 아님"(`:78`)이라 인정하면서 부속결정에선 메모리 차이를 아키텍처로 귀속.
- **왜 blocker**: 설계부 수준 결함. 목표 진술이 내부 모순이고 게이트가 오귀인 숫자 출하를 허용. 2번 질문("미결 상태로 돌리면 해석 오염되는가")의 답은 **예, 순수 아키텍처 해석은 오염**.
- **제안**: 목표를 하나로 확정 — (A) "실제 배포 프로파일"이면 부속결정에서 "아키텍처 비대칭" 표현을 버리고 모든 지표에 featureset 기여 명시 귀인, 또는 (B) "아키텍처 비교"면 featureset 고정 통제 arm을 필수 게이트로. 각주 한 줄로 끝내지 않는다.

> **[reviser 응답]** 해소: 결정 4에서 **목표를 (A) "실제 배포 프로파일"로 확정**(미결 종료). 헤드라인은 (아키텍처 × featureset) 결합 프로파일이며 "순수 아키텍처 운영비"가 아님을 명시. 필수 통제 3가지 박음 — (1) "아키텍처 비대칭" 단독 헤드라인 폐기, (2) 모든 지표에 featureset 기여 vs 아키텍처 기여 분해 귀인, (3) **귀인을 뒷받침하는 통제 분해 arm(GRU/9 vs XGB/9)을 필수 PASS 게이트로** — 없이 배포 arm(GRU/9 vs XGB/18) 숫자만 출하 시 FAIL. (B)를 헤드라인으로 안 쓴 이유(문서 포지셔닝 = 실배포 운영비)와 (B)의 통제 arm을 (A)의 분해 수단으로 흡수한 근거도 명시 — 목표는 하나(=A). 부속 결정도 "결합 프로파일"로 개정. (decisions.md 결정 4 "★featureset 목표 확정" · 부속 결정 · PASS 게이트 4)

### major

- **M1. XGB "같은 계약" 응답 스키마 불완전 명세** — 결정 2.3은 "응답 `p`·`alarm`"만 계약이라 하나(`:44`), 실제 응답은 `{patient_id, p, alarm, featureset}`(`app.py:103-104`). XGB 최소 앱이 응답 전체 키를 복제해야 함. 결정 2에 "요청+응답 전체 스키마 일치"로 명시 권고.

> **[reviser 응답]** 해소: 결정 2.3을 "같은 `/predict` 요청/응답 **전체 스키마**"로 개정. 요청 `{patient_id: str, features: dict[str, float|None]}`, 응답 `{patient_id, p, alarm, featureset}` 네 키 전부를 [확인됨: `app.py:77-79,103-104`]로 명시하고 XGB 최소 앱의 응답 전체 복제를 요구. PASS 게이트 1에도 반영. (decisions.md 결정 2.3 · PASS 게이트 1)

- **M2. 부팅 워밍업 정의가 캘리브레이션 비용을 빠뜨림** — 결정 4(`:73`)는 워밍업을 "첫 predict 시 모델 로드"로만 규정. 실제 첫 요청은 300-trial 드리프트 캘리브레이션(`app.py:61-66`)까지 유발. XGB 최소 앱엔 없어 워밍업 프로파일이 구조적으로 다름. 워밍업 컷을 "정상상태 도달"로 실측 정의하고 부팅 비용 항목 대칭화.

> **[reviser 응답]** 해소: 결정 4 워밍업 항목을 "정상상태 워밍업"으로 재정의. 첫 요청이 lazy-load뿐 아니라 `synthetic.calibrate(n_trials=300)`까지 유발함을 [확인됨: `app.py:61-66,73`]로 명시하고, 워밍업 컷을 "연속 K요청 p95가 안정 범위로 수렴한 시점까지 실측 제외"로 정의. GRU 부팅비용(로드+캘리브레이션) vs XGB 부팅비용(로드만)은 구조가 달라 **정상상태와 분리해 별도 항목**으로 대칭 기재. (decisions.md 결정 4 "정상상태 워밍업" · PASS 게이트 3)

### minor

- **m1. [확인됨: 메모리 — CPU 환경](`:88`) 출처 모호** — "메모리"가 auto-memory `MEMORY.md`인지 불명. 1차 아티팩트로 재확인 권장. 검증 불가 [확인됨]은 [검증 필요]로 강등.

> **[reviser 응답]** 해소: 결정 5의 해당 근거를 [확인됨]에서 [검증 필요: 1차 아티팩트로 재확인 — 출처 불명, 강등]으로 강등. (decisions.md 결정 5 근거)

- **m2. 메모리 측정 [검증 필요](`:62`)는 설계부 수준 수용 가능** — 단 B2로 인해 "무엇을 재는가"가 먼저 확정돼야 측정법이 정해진다는 선후관계 명시.

> **[reviser 응답]** 해소: 결정 3 메모리 근거에 "무엇을 재는지(순수 추론 RSS vs 계측 부속물 포함)는 결정 4 B2가 먼저 확정 → 측정법은 그 뒤"라는 선후관계를 명시. (decisions.md 결정 3 근거)

### 판정

**blocker 3건 → HOLD.** 설계부 복귀, 세 blocker 해소 후 재검토.

> **[reviser 라운드 1 종료]** blocker 3건(B1·B2·B3) + major 2건(M1·M2) + minor 2건(m1·m2) 모두 decisions.md v2에 반영. 각 항목 [reviser 응답] 참조. 다음 redteam 라운드가 "진짜 해소됐는지" 대조 요망 — 특히 (1) 결정 3의 network 분해식이 서버 히스토그램 관측 경계와 실제 일치하는지, (2) arm-1/arm-2 계측 표면 대칭이 구현 가능한지, (3) 통제 arm(GRU/9 vs XGB/9)이 featureset 기여를 정말 분리하는지.

---

## 라운드 2

- 대상 commit: `320a8c0` (main) — decisions.md v2 (reviser 보완)
- 검토일: 2026-07-01
- 핵심 질문: 라운드 1 blocker 3건이 표면만 덮인 게 아니라 코드와 정합하게 해소됐는가 / reviser가 대조 요망한 3지점(분해식·arm 대칭·통제 arm) 실현성
- 판정: **HOLD — blocker 3건 (신규)**

### 라운드 1 blocker 재판정

- **B1 (서버 히스토그램 부재 거짓 [확인됨]) → 부분 해소 / 새 문제.** [확인됨: 히스토그램 부재]를 삭제하고 `serve_predict_latency_seconds` 실재를 정정한 것은 코드와 정합(`metrics.py:18,46`, `app.py:96-98` 대조 완료 — PASS). **그러나** 그 자리에 박은 network 분해식(`decisions.md:62,68` "network+직렬화 = client − 서버 히스토그램")이 히스토그램의 실제 관측 경계와 어긋난다 → **NB1**. B1의 목적(network를 한계로 넘기지 않고 실제 분해)은 미달성.
- **B2 (계측 비대칭) → 부분 해소 / 새 문제.** arm-1/arm-2 개념과 메모리 3기여 귀인을 신설한 것은 방향이 맞다. **그러나** (a) arm-2(순수 추론)를 재사용 GRU 경로에서 켜고 끌 토글이 코드에 없고 결정 1·2의 격리 원칙과 충돌 → **NB3**, (b) 메모리 3기여 귀인이 XGB 자체의 lookback 상태를 누락 → **NB2**.
- **B3 (featureset 목표 모순 + 오귀인 게이트) → 설계 목표 통일은 해소, 통제 arm 실현성은 NB2에 종속.** 목표를 (A) 실배포 프로파일로 단일화하고 통제 arm(GRU/9 vs XGB/9)을 필수 PASS 게이트로 승격한 것은 내부 모순을 제거했다(PASS). XGB/9 아티팩트도 실재(`mlruns/1/3e21f380b380422d8d52f78904e54ad4/artifacts/model/xgboost_vitals.ubj`)해 아티팩트 관점 실현 가능. **단** 통제 arm이 "featureset 기여만 분리"한다는 전제는 XGB 서빙이 stateless라는 가정에 의존하는데 그 가정이 거짓(NB2)이라, 통제 arm 차이에 lookback-state 기여가 섞여 순수 분리가 깨진다.

### PASS (라운드 2에서 코드로 재확인)

- **reviser가 새로 단 [확인됨] 태그는 코드와 정합(거짓 [확인됨] 재발 없음).**
  - 응답 스키마 `{patient_id, p, alarm, featureset}` [확인됨: `app.py:103-104`], 요청 `{patient_id: str, features: dict[str, float|None]}` [확인됨: `app.py:77-79`] — 정확.
  - 워밍업 300-trial 캘리브레이션 [확인됨: `app.py:61-66,73`], per-feature 루프·drift window [확인됨: `metrics.py:52-56`, `window.py:26-34`, `app.py:102`], featureset 9/18 [확인됨: `config.py:51-52`] — 모두 정확.
- **히스토그램 관측 경계 서술 자체는 정확** — 결정 3의 "`serve_predict_latency_seconds`가 순수 내부 추론 시간을 관측"은 참. 오류는 이 경계에서 도출한 분해식(NB1)이지 경계 서술이 아니다.

### blocker

#### NB1. network 분해식이 히스토그램 관측 경계와 산술적으로 어긋남 (B1 보완이 만든 새 결함)
- **문제**: 결정 3(`decisions.md:62`, `:68`, PASS 게이트 2 `:117`)은 **"network+직렬화 = client 벽시계 − 서버 내부 히스토그램"**을 분해식으로 단정한다. 그러나 서버 히스토그램은 `predict()` 구간만 관측하고, `/predict` 핸들러의 **추론 후 부가작업**(per-feature 루프 + drift window add)은 히스토그램 밖·client 벽시계 안에 있다. 따라서 실제로는 `client − 히스토그램 = network + 직렬화 + (per-feature 루프 + window.add) + 프레임워크 serialize`이며, 부가작업이 통째로 "network"로 오귀인된다.
- **근거**:
  - `app.py:98` `metrics.record(time.perf_counter() - t0, ...)` — 첫 인자는 `predict()` 반환 직후 평가되어 고정. `metrics.py:46` `LATENCY.observe(latency_s)`는 그 고정값을 관측 → 히스토그램 = **predict()만**.
  - `metrics.py:52-56` per-feature 루프(GRU 9회 / XGB 18회)와 `app.py:102` `get_window().add`는 `LATENCY.observe` **이후** 실행 → 히스토그램 미포함, client 벽시계엔 포함.
  - 이 부가작업은 **비대칭**(GRU 9 vs XGB 18 루프)이라 network 추정치가 XGB 쪽에서 체계적으로 부풀려짐 — B1이 없애려던 "localhost network 못 가름"보다 더 나쁜 편향 수치 생성.
- **왜 blocker**: B1 보완의 핵심 산출물(network 분해)이 arm-1에서 정의부터 틀렸다. 결정 3은 분해식을 arm 조건 없이 무조건 서술(`:62`). 이 수치가 리포트 헤드라인(PASS 게이트 2·6)에 박히면 오귀인이 출하된다.
- **제안**: (a) 히스토그램 관측 경계를 핸들러 전체(부가작업 포함)로 옮기거나 별도 히스토그램 추가해 client 벽시계와 정렬, 또는 (b) 분해식을 arm-2(부가계측 OFF)에만 적용된다고 명시하고 arm-1에는 network 추정 주장 안 하도록 결정 3·PASS 게이트 2 수정. 어느 쪽이든 "부가작업 = network로 흡수되는 잔차"임을 정직하게 기재.

> **[reviser 응답]** 해소(제안 b 채택): 결정 3에 "★히스토그램 관측 경계 = `predict()`만, 핸들러 후처리는 밖" 항목을 신설 — `metrics.record`의 첫 인자가 predict() 반환 직후 고정되고 `LATENCY.observe`가 그 값을 관측하며, per-feature 루프·`window.add`는 그 뒤 실행됨을 [확인됨: `app.py:96-98,102`·`metrics.py:43-56` 실행 순서]로 코드 대조 명시. 그 위에 "★잔차 정직 재명명 + 분해식 arm-2 한정" 항목을 박음: (1) `client − 히스토그램`을 **"client−server 잔차(= network + 직렬화 + 핸들러 후처리)"**로 재명명(mn1), (2) arm-1에서 이 잔차를 "network"로 헤드라인 출하 **금지**, (3) network 추정치는 **arm-2(부가계측 OFF)에서만 헤드라인**(핸들러 후처리가 게이트로 꺼져 잔차가 network+직렬화로 좁혀짐), (4) 핸들러 후처리분은 (arm-1 잔차 − arm-2 잔차) 차분으로 별도 귀인. PASS 게이트 2도 동일 문구로 개정("network는 arm-2에서만 헤드라인"). 제안 (a)(경계 이동)는 프로덕션 `serve_predict_latency_seconds`의 순수 추론 의미를 바꿔 관측 회귀를 유발하므로 기각하고 (b)로 잔차 폭을 client 벽시계 쪽에서 좁히는 방식 채택(결정 3 고려한 대안에 명시). (decisions.md 결정 3 전체 · PASS 게이트 2 · 검토 상태 §)

#### NB2. XGB "stateless" 주장이 학습 피처 파이프라인과 정면 배치 — XGB 서빙 lookback 상태 미설계
- **문제**: 부속 결정(`decisions.md:110`)은 "XGB는 stateless(요청 독립)"라 규정하고 이를 메모리 3기여 분해의 축("stateful hidden state는 GRU만")으로 삼는다. 그러나 XGB 챔피언 입력은 단일 timestep raw가 아니라 **8시간 lookback 요약통계**다. XGB 서빙이 학습과 같은 입력을 만들려면 환자별 최근 8행 raw 버퍼가 필요하며, 이는 **동시 환자 수에 따라 증가하는 per-patient 상태** — GRU에만 귀속시킨 바로 그 특성이다.
- **근거**:
  - `config.py:61-62` `LOOKBACK = 8`, `TREE_STATS`(7종).
  - `features.py:30-59` `lookback_summary`: `(T,F) → (T, F*7)`, row t = 윈도우 `[t-7..t]` 요약. vitals9 → 63차원.
  - `tree.py:1-6` "Input = per-timestep lookback summaries". 한 timestep 채점에 직전 7행 필요.
  - 대비: GRU는 hidden state로 O(1) 진행(`predictor.py:41-51`), XGB는 원시 윈도우 자체를 보관해야 요약 산출.
- **왜 blocker**: (1) 부속 결정의 load-bearing 주장("stateless")이 거짓 → 문서 중심 발견(stateful vs stateless)이 허위 전제. (2) 결정 2 "같은 `/predict` 계약"이 XGB에서 성립하려면 단일 timestep 요청으로 63차원 요약을 재구성하는 방법 설계 필요한데 DDD 침묵 — 버퍼 없이 1행이면 mean=min=max=last, delta/var 퇴화로 train-serve skew. (3) 메모리 3기여 분해가 XGB lookback 버퍼 기여를 빠뜨려 통제 arm(GRU/9 vs XGB/9)의 순수 featureset 분리도 오염.
- **제안**: 결정 2에 "XGB 최소 서빙은 환자별 8행 lookback 버퍼를 유지해 `features.lookback_summary`로 63차원 입력 구성" 의존 명시(설계부 수준: 상태 필요성·소스 식별까지). 부속 결정의 "XGB stateless" 폐기, 메모리 대비를 "GRU hidden state vs XGB lookback 버퍼(둘 다 환자 수 증가)"로 재서술. 3기여 분해에 XGB lookback-state 기여 추가.

> **[reviser 응답]** 해소: 세 지적 모두 반영. (1) **의존 명시** — 결정 2에 "★XGB 서빙은 stateless가 아니다" 항목 신설, XGB 최소 서빙이 환자별 최근 8행 raw 버퍼를 유지해 `data/features.lookback_summary`로 vitals9→63/vitals_labs18→126차원을 매 요청 재구성함을 [확인됨: `config.py:61-62`·`features.py:30-59`(row t=윈도우 `[t-7..t]`)·`train/tree.py:3`]로 명시. 버퍼 자료구조·소멸 정책은 핸드오프로 미룸(설계부는 상태 필요성·소스까지). (2) **계약 공백 메움** — `/predict` 요청은 GRU와 동일하게 단일 timestep 1행 유지, 63차원 요약은 **서버가 자기 버퍼로 재구성**(클라이언트가 8행 안 보냄, GRU hidden state와 대칭). 버퍼<8행이면 사용 가능 행으로만 NaN-aware 요약(`features._windows` 앞 NaN 패드 `features.py:25`), 버퍼 없이 1행이면 mean=min=max=last·delta/var 퇴화 → skew이므로 버퍼 유지가 **필수**임을 명문화. (3) **메모리 대비 재서술** — 결정 4 메모리 귀인의 "state 축"을 GRU 전용에서 **양쪽(GRU hidden state vs XGB lookback 버퍼, 둘 다 환자 수 증가)**으로 재정의, 부속 결정에서 "XGB stateless" v2 서술을 명시 폐기(허위 전제), 통제 arm(GRU/9 vs XGB/9) 잔차에 state 차이가 섞임을 결정 4·PASS 게이트 4에 명시. MJ1(아티팩트 소스)도 결정 2에 함께 식별. (decisions.md 결정 2 "★XGB stateless 아님"·"★XGB 아티팩트 소스" · 결정 4 메모리 귀인 · 부속 결정 · PASS 게이트 1/4/6)

#### NB3. arm-2(순수 추론)가 재사용 GRU 경로에서 구현 불가 — 결정 1 격리·결정 2와 충돌, 의존 미식별
- **문제**: 결정 4(`decisions.md:81`)의 arm-2는 "두 서빙 모두 부가 계측을 **끄고** 측정"을 요구. XGB 최소 앱은 새로 짜니 토글 가능하나, **GRU 서빙은 "기존 재사용"**(`:43`)이라 코드가 고정. 현재 GRU `/predict`엔 부가작업을 끌 스위치가 없다.
- **근거**:
  - `app.py:98` `metrics.record(...)` 무조건 호출 → `metrics.py:52-56` per-feature 루프 무조건. `app.py:102` `get_window().add` 무조건.
  - 유일한 토글 `SERVE_PER_PATIENT_GAUGE`(`metrics.py:38-40`)는 `PRED_PROB_LATEST` gauge만 가드, per-feature 루프·drift window는 미가드.
  - 따라서 GRU arm-2 실행하려면 `app.py`/`metrics.py` 수정 필요 → 결정 1(`:31` "서빙 프로덕션 경로 미변경")·PASS 게이트 1(`:116` grep 강제)·결정 2 미결(`:51` "분기 플래그가 GRU 경로 오염 금지")과 충돌.
- **왜 blocker**: B2 해소책(arm-1/arm-2 이분)이 GRU 측에서 실현 불가거나, 실현하려면 문서가 금지한 프로덕션 서빙 수정 요구 — 결정 4와 결정 1/2 사이 내부 모순. 필요한 의존(계측 토글) 미식별.
- **제안**: 셋 중 하나 명시. (a) arm-2 토글을 "관측성 전용, 예측/추론 로직 불변"으로 규정하고 결정 1 격리 예외로 명문화(grep 게이트 문구도 예외 반영), (b) arm-2를 벤치 전용 GRU 최소 앱(계측 없는 사본)으로 돌린다 하되 결정 2와 정합 재서술, (c) arm-2를 범위 외로 낮추고 arm-1만 헤드라인. "끄고 측정"만 적고 토글 소재 비우면 핸드오프에서 재폭발.

> **[reviser 응답]** 해소(옵션 a 채택): 결정 1에 "★격리 예외 = 관측성 전용 게이트" 항목 신설. 격리 원칙을 "프로덕션 경로 절대 불변"에서 **"예측/추론 로직 불변, 관측성은 env-게이트로 가감 가능"**으로 정밀화. 도입하는 env 게이트가 가드하는 것은 **per-feature INPUT 루프(`metrics.py:52-56`) + `get_window().add`(`app.py:102`)에 한정**, **`predict()`·`_row_from`·응답 dict·`LATENCY`(`serve_predict_latency_seconds`) 관측은 불변**임을 명시([확인됨: 현재 무조건 호출 `app.py:98,102`, 유일 토글 `SERVE_PER_PATIENT_GAUGE`는 gauge만 가드 `metrics.py:38-40`]). 이는 코드베이스에 **이미 있는 옵트인 게이트 패턴의 확장**이지 새 아키텍처가 아님을 근거로 명시. 옵션 (b)(벤치 전용 사본)는 결정 2 "GRU=기존 재사용"과 어긋나고 프로덕션과 코드가 갈라져 "프로덕션 서빙을 실측한다"는 목적 훼손으로, (c)(arm-1만)는 B2 최대 교란원 통제 포기로 결정 1 고려한 대안에서 각각 기각 명시. 결정 4 arm-2 항목에 "토글 소재 = 이 게이트"를 못박고, PASS 게이트 1을 "예측/추론 로직 불변(grep) + 관측성 게이트 예외 허용"으로, 게이트 3을 "게이트 소재=결정 1 예외 env 스위치"로 개정. (decisions.md 결정 1 "★격리 예외" · 결정 4 "★arm-2 토글 소재" · PASS 게이트 1/3)

### major

- **MJ1. XGB 서빙 아티팩트 소스 미식별** — 통제 arm은 `xgboost_vitals`(9)·`xgboost_vitals_labs`(18) 둘 다 필요. 둘 다 `mlruns/1/…`에만 있고 `deploy/artifacts/`엔 GRU 별칭만. DDD가 XGB 최소 앱이 어느 소스(mlruns run dir vs 승격 별칭)에서 로드하는지 미식별. "XGB 아티팩트 소스·preprocess.json 경로" 의존 명시 권고.

> **[reviser 응답]** 해소: 결정 2에 "★XGB 아티팩트·전처리 소스 식별" 항목 신설. `deploy/artifacts/`엔 GRU 별칭만 있고 XGB 승격 별칭 없음을 확인[확인됨: `deploy/artifacts/`에 `gru_vitals*`만], 소스를 **MLflow run 디렉토리**로 확정 — `mlruns/1/3e21f380…/artifacts/model/xgboost_vitals.ubj`(9)·`mlruns/1/fe64aac5…/artifacts/model/xgboost_vitals_labs.ubj`(18) + 각 run `artifacts/preprocess.json`(keys `featureset, scale_pos_weight, tau, hp, note`)이 featureset·alarm 임계 `tau` 제공 [확인됨: 파일 실재·JSON keys 실측]. XGB는 트리 NaN-native라 정규화 통계 불필요(preprocess.json에 mean/std 없음이 정상)도 명시. run id 주입 방식(하드코딩 vs 설정)은 핸드오프. PASS 게이트 1에도 소스 반영. (decisions.md 결정 2 "★XGB 아티팩트·전처리 소스" · PASS 게이트 1)

### minor

- **mn1. 결정 3 분해식 명칭 정직화** — NB1 수정 시 "network+직렬화" 대신 "client−server 잔차(= network + 직렬화 + 핸들러 후처리)"로 부르면 arm-1에서도 오해 없이 성립. 잔차 내 부가작업분을 arm-2와의 차분으로 재분리하는 방식을 리포트 한계에 명기.

> **[reviser 응답]** 해소: NB1 응답과 함께 반영 — 결정 3에서 잔차를 "client−server 잔차(= network + 직렬화 + 핸들러 후처리)"로 재명명하고, 잔차 내 핸들러 후처리분을 (arm-1 잔차 − arm-2 잔차) 차분으로 재분리하는 방식을 결정 3·PASS 게이트 2·6(한계)에 명기. (decisions.md 결정 3 · PASS 게이트 2·6)

### 판정

**라운드 2 blocker 3건(NB1·NB2·NB3) → HOLD.** 라운드 1의 B1·B2는 표면 정정에 그쳐 각각 분해식 오류(NB1)·arm-2 실현 불가(NB3)라는 새 결함을 남겼고, B3의 featureset 목표 통일은 해소됐으나 통제 arm 실현성이 XGB stateless 허위 전제(NB2)에 발목 잡힌다. 세 blocker 모두 설계부 수준이므로 핸드오프 진행 불가. reviser 복귀 → NB1·NB2·NB3 해소 후 라운드 3 재검토.

> **[reviser 라운드 2 종료]** blocker 3건(NB1·NB2·NB3) + major MJ1 + minor mn1 모두 decisions.md v3에 근본 반영(코드 직접 대조 — `app.py:96-98,102`·`metrics.py:38-56`·`config.py:61-62`·`features.py:25,30-59`·`train/tree.py:3`·`deploy/artifacts/`·`mlruns/1/…preprocess.json` keys 실측). 각 항목 [reviser 응답] 참조. 표면 재봉합이 아니라 (1) 히스토그램 관측 경계를 코드 실행순서로 명시하고 network 분해를 arm-2 한정으로 축소, (2) XGB stateless 허위 전제를 폐기하고 lookback 버퍼 상태를 결정 2 의존으로 승격, (3) arm-2 토글을 결정 1 격리 예외(관측성 전용)로 명문화 — 세 결정을 다시 세움. 문서 전체에서 옛 주장(무조건 network 분해·XGB stateless) 잔재 없음 확인(grep). 다음 redteam 라운드가 대조 요망: ①arm-2 잔차가 실제 network+직렬화로 좁혀지는지 ②XGB lookback 버퍼가 계약 유지하며 서버측 상태로 성립하는지·초기 skew ③관측성 게이트의 예측 로직 불변이 grep 증명 가능한지 ④통제 arm 잔차 state 기여 분리 실측 가능성.

---

## 라운드 3

- 대상 commit: `320a8c0` (main) — decisions.md v3 (reviser 보완)
- 검토일: 2026-07-01
- 핵심 질문: 라운드 2 blocker(NB1·NB2·NB3)가 표면 봉합이 아니라 코드와 정합하게 해소됐는가 / v3 수정이 새 결함·문서 내부 모순을 낳지 않았는가 / XGB 챔피언 재구성 의존 사슬을 끝까지 추적했는가
- 판정: **HOLD — blocker 1건 (신규, B-R3-1). 라운드 3 = 규칙상 마지막 라운드 → 사람 에스컬레이션.**

### 라운드 2 blocker 재판정 (코드 대조)

- **NB1 (network 분해식이 히스토그램 관측 경계와 어긋남) → 해소됨.** 히스토그램 경계 서술이 코드와 정합. `metrics.py:46` `LATENCY.observe(latency_s)`가 `predict()` 반환 직후 고정된 `perf_counter()-t0`(`app.py:96-98`)를 관측하고 per-feature 루프(`metrics.py:52-56`)·`get_window().add`(`app.py:102`)는 그 **뒤** 실행 — 히스토그램은 predict() 구간만. [확인됨: `metrics.py:43-56` 실행순서] 잔재명 정직화·arm-2 한정 헤드라인·(arm-1−arm-2) 차분 귀인 논리 성립. **PASS.**
- **NB2 (XGB "stateless" 허위 전제) → 해소됨.** lookback 버퍼 재구성 계약이 학습 피처 정의와 정합함을 확인: 피처 순서 일치(`config.py:62` TREE_STATS = `features.py:58` concat = `features.py:64` summary_columns stat-major), 윈도우 정의 일치(`features.py:21-27` row t=[t-7..t] NaN 패드), **clip skew 없음**(트리 경로는 학습·크로스사이트 모두 raw를 그대로 lookback_summary에 — `crosssite.py:42-43`, XGB는 NaN-native). 메모리 3기여 state 축 양쪽 재정의·부속결정 "XGB stateless" 폐기 반영. **PASS.**
- **NB3 (arm-2 토글 재사용 GRU 경로 구현 불가) → 해소됨(설계부 수준).** env-게이트가 예측/추론 로직을 건드리지 않고 삽입 가능함을 확인 — 게이트 대상(per-feature 루프 `metrics.py:52-56`·`get_window().add` `app.py:102`)은 `LATENCY.observe`·`predict()`·응답 dict와 물리적으로 분리된 라인. 기존 `_per_patient_enabled()`(`metrics.py:38-40`) 옵트인 패턴과 동형. grep 게이트를 "predict·응답·LATENCY 불변"으로 좁힌 것과 모순 아님. **PASS.**
- **MJ1 (XGB 아티팩트 소스 식별) → 부분 해소 / 새 blocker.** `.ubj` 경로·`deploy/artifacts/` GRU 별칭만·preprocess.json keys 실측 확인은 정확. **그러나 챔피언 재구성에 필요한 `best_iter` 의존이 식별에서 빠졌다 → B-R3-1.**

### PASS (라운드 3에서 코드로 재확인)

- v3가 새로 단/유지한 [확인됨] 태그는 코드와 정합(거짓 [확인됨] 재발 없음). `config.py:61-62`·`features.py:25,30-59`·`tree.py:3`·`app.py:77-79,103-104`·`metrics.py:18,38-56`·`.ubj`/preprocess.json 실측 — 전부 대조 통과.
- 문서 전역 잔재 없음. 옛 주장("무조건 network 분해"·"XGB stateless"·"순수 아키텍처 운영비")이 긍정 주장으로 남은 곳 없음. 헤드라인 = (아키텍처×featureset) 배포 프로파일로 일관.

### blocker

#### B-R3-1. XGB 챔피언 재구성에 `best_iter`(iteration_range 절단)가 load-bearing인데 DDD가 의존으로 식별하지 않음 — `.ubj`+`tau`만으론 챔피언 latency·확률 재현 불가
- **문제**: 결정 2 MJ1(`decisions.md:53`)·PASS 게이트 1(`:133`)은 XGB 서빙 소스를 "`.ubj` + preprocess.json(tau)"로만 식별. 그러나 H2/H3 챔피언은 **early-stopping best_iteration까지만** 트리를 쓴다. 네이티브 `.ubj`를 로드해 `Booster.predict()`를 그냥 부르면 **전체 400 트리**로 추론 — 챔피언과 다른 모델.
- **근거(의존 사슬 추적)**:
  - `tree.py:69-75` `booster_predict`가 `iteration_range=(0, best_iter+1)`로 절단 — 챔피언 추론의 정의.
  - `crosssite.py:60,65` `score_tree_frozen(booster, model_name, best_iter, tau, …)` → best_iter 명시 주입.
  - `h3b_crosssite.py:144,158,162` best_iter를 **MLflow metric `metrics.best_iter`**에서 가져와 주입(`.ubj`·preprocess.json 아님).
  - `h2b_train_trees.py:99` 저장은 네이티브 부스터만. preprocess.json엔 best_iter 없음 [확인됨: 파일 keys 실측]. best_iter는 `h2b_train_trees.py:183`에서 metric으로만 로깅.
- **왜 blocker**: (1) 헤드라인 지표 오염 — latency는 트리 수에 선형 비례, 절단 없이 400 트리 서빙 시 XGB latency 체계적 부풀림 → 목표(A) "실제 배포 프로파일"과 배치. (2) tau 오정렬 — tau는 best_iter-절단 확률분포에서 캘리브레이션됨, 전체 트리 확률에 적용 시 alarm 판정 달라짐. (3) DDD가 tau·featureset은 식별하면서 동급 의존 best_iter만 누락 — 구현 디테일이 아니라 **입력 의존 식별**(설계부 몫).
- **제안**: 결정 2 "★XGB 아티팩트·전처리 소스 식별"에 `best_iter` 의존 추가 — 소스 = run MLflow metric `metrics.best_iter`, XGB 서빙은 `iteration_range=(0, best_iter+1)`로 절단. PASS 게이트 1에도 "XGB 소스 = `.ubj` + `tau` + `best_iter`"로 반영.

> **[reviser 응답]** 해소: 결정 2 "★XGB 아티팩트·전처리 소스 식별"을 **`.ubj` + `tau` + `best_iter` 세 조각**으로 재구성. `best_iter`를 **챔피언 재구성의 load-bearing 의존**으로 신설 — 절단 없이 전체 트리로 추론하면 latency 부풀림·확률 이동·tau 오정렬로 챔피언과 다른 모델임을 명시하고, 소스가 **run metric `metrics.best_iter`**(`.ubj`·preprocess.json 아님)임을 코드 대조로 못박음: [확인됨: `src/sepsis/train/tree.py:69-75` `booster_predict`가 `iteration_range=(0, int(best_iter)+1)` 절단; `eval/crosssite.py:60,65` `score_tree_frozen(…, best_iter, …)` 주입; `h3b_crosssite.py:144,158,162` best_iter를 run metric에서 읽어 주입; `h2b_train_trees.py:183`이 metric으로만 로깅·`:189` log_dict(preprocess.json)엔 tau·featureset만; preprocess.json keys `['featureset','scale_pos_weight','tau','hp','note']` 실측]. 설계부 수준(의존 존재·소스 식별)까지만 확정하고 **값 주입 경로(하드코딩 vs 벤치설정 vs run metric 조회) 구현은 핸드오프로 위임**함을 명문화. PASS 게이트 1도 "XGB 서빙 소스 = `.ubj` + `tau` + `best_iter`, `iteration_range=(0, best_iter+1)` 절단"으로 개정. (decisions.md 결정 2 "★XGB 아티팩트·전처리 소스 식별" 3항 · PASS 게이트 1)
> 정합성: 기존 `.ubj`+`tau` 서술과 모순 없이 3조각으로 일관화, NB2 lookback 버퍼 재구성 계약과 충돌 없음(best_iter는 booster.predict 트리 절단, 버퍼는 입력 재구성 — 독립 의존). 참고: review가 인용한 `src/sepsis/models/tree.py`는 실제 경로 `src/sepsis/train/tree.py`이며 내용 동일 [확인됨].

### major

#### M-R3-1. XGB 서버 히스토그램 관측 경계가 GRU와 비대칭일 위험
- **문제**: 결정 3(`:75`)은 "XGB도 GRU와 동일 관측 경계(predict 감싸기)"라 하나, GRU `predict()`가 내부에 `StreamPreprocessor.step`(ffill→fill_mean→clip→z-score)을 포함 [확인됨: `predictor.py:44`]. XGB 대칭이 되려면 히스토그램이 **버퍼→lookback_summary 재구성(8×F 윈도우 7종 통계, numpy) + booster.predict**를 함께 감싸야. XGB 앱이 요약 재구성을 핸들러에 인라인하고 booster.predict만 감싸면 두 "서버 히스토그램"이 다른 범위를 재 NB1 비대칭이 다른 형태로 재발.
- **제안**: 결정 3에 "XGB `serve_predict_latency_seconds`는 버퍼→lookback_summary 재구성 + booster.predict를 함께 감싼다(GRU predict의 StreamPreprocessor 포함과 대칭)" 명시.

> **[reviser 응답]** 해소: 결정 3에 "★XGB 히스토그램 관측 경계" 항목 신설. GRU `predict()`가 내부에 전처리를 포함함을 [확인됨: `serve/predictor.py:44` `z = self.pre.step(pid, row)`가 predict 안에서 실행]으로 명시하고, XGB `serve_predict_latency_seconds`는 **버퍼→`lookback_summary` 재구성(윈도우 7종 통계) + `booster.predict`(best_iter 절단)를 함께** 감싸야 GRU의 "전처리 포함 predict"와 동형 경계가 됨을 못박음. `booster.predict`만 감싸면 GRU=전처리포함/XGB=추론만으로 두 서버 히스토그램 범위가 달라져 **NB1형 비대칭 재발**임을 명시. PASS 게이트 2도 동일 문구로 개정. (decisions.md 결정 3 "★XGB 히스토그램 관측 경계" · PASS 게이트 2)

### minor

- **mn-R3-1.** 문서 제목/포지셔닝의 "아키텍처별 운영비용"(`:1`)이 (A) 목표와 어휘 마찰. 본문은 헤드라인=(아키텍처×featureset)로 확정해 실질 모순 없으나, 제목에 "(featureset 결합 배포 프로파일)" 한 마디 덧대면 무해화.

> **[reviser 응답]** 해소: 문서 제목을 "…아키텍처별 운영비용 벤치마크 **(featureset 결합 배포 프로파일)**"로 덧대 (A) 목표와 어휘 마찰 무해화. (decisions.md 제목)

### 판정

**라운드 3 blocker 1건(B-R3-1) → HOLD.**
- 라운드 2 blocker 3건(NB1·NB2·NB3)은 **모두 해소됨** — 코드 구조가 실제로 수용(표면 봉합 아님).
- 그러나 MJ1 의존 사슬 추적에서 **`best_iter`가 챔피언 재구성에 load-bearing인데 식별 누락** — 목표(A)와 배치되는 설계부 결함.
- **라운드 3 = 규칙상 마지막 라운드. blocker>0 → 자동 통과 불가. 사람 에스컬레이션(푸시 보류).** 사람 판단 지점: B-R3-1을 설계부에 반영(best_iter 의존 명시)할지, 예외적으로 핸드오프 범위로 위임할지. (레드팀 권고: `.ubj`·`tau`와 동급 의존이므로 설계부 반영이 정합.) M-R3-1은 같은 보완 라운드에서 함께 처리 권장.

> **[reviser 라운드 3(round 4 커밋) 종료]** 사람이 3라운드 캡을 예외 승인한 마지막 보완 라운드. 지정 항목만 근본 해소(범위 확장 없음): blocker **B-R3-1**(best_iter를 `.ubj`·`tau`와 동급 의존으로 설계부 반영 — 결정 2 3항 + PASS 게이트 1), major **M-R3-1**(XGB 히스토그램 경계를 버퍼→lookback_summary+booster.predict로 GRU와 대칭 — 결정 3 + PASS 게이트 2), minor **mn-R3-1**(제목). 모든 새 사실 주장은 코드 직접 대조분만 [확인됨](`train/tree.py:69-75`·`crosssite.py:60,65`·`h3b_crosssite.py:144,158,162`·`h2b_train_trees.py:183,189`·`predictor.py:44`·preprocess.json keys 실측) — 거짓 [확인됨] 없음. 다음 redteam이 대조 요망: best_iter 의존이 결정 2 lookback 버퍼 계약·부속결정과 모순 없이 일관화됐는지, XGB 히스토그램 경계 대칭이 NB1 잔차 분해와 정합한지.

---

## 라운드 4

- 대상 commit: `320a8c0` (main) — decisions.md **v4** (reviser 보완)
- 검토일: 2026-07-01
- 범위: 라운드 3 잔여 blocker **B-R3-1**(best_iter 의존·소스)·major **M-R3-1**(XGB 히스토그램 경계 대칭)과 그 파급만
- 판정: **HOLD — blocker 1건 (신규, B-R4-1).** 사람이 캡을 예외 승인한 최종 검증 라운드이나 B-R3-1의 **소스 식별부**가 저장소 상태와 모순.

### 라운드 3 잔여 항목 재판정 (코드·아티팩트 직접 대조)

- **B-R3-1 (best_iter 의존 식별) → 부분 해소 / 새 blocker.**
  - **의존 식별부 = 해소됨.** "챔피언 재구성은 전체 트리가 아니라 best_iteration까지, `iteration_range=(0, best_iter+1)` 절단"은 코드와 정확 [확인됨: `train/tree.py:74-75` `rng = (0, int(best_iter)+1) if best_iter and best_iter >= 0 else None` → `booster.predict(..., iteration_range=rng)`]. 채점 경로 주입 정합 [확인됨: `eval/crosssite.py:60,65`]. **PASS.**
  - **소스 식별부 = 미해소 → B-R4-1.** 지목 소스(run metric `metrics.best_iter`)가 저장소에 영속돼 있지 않고, ".ubj에도 없다 [확인됨]"이 .ubj 임베드 `best_iteration`과 모순.
- **M-R3-1 (XGB 히스토그램 경계 대칭) → 해소됨.** GRU `predict()`가 전처리 내포 정확 [확인됨: `predictor.py:44` `z = self.pre.step(pid, row)`가 predict 내부, `preprocess_rt.py:45-49` ffill→fill_mean→clip→z]. XGB 경계를 "버퍼→lookback_summary 재구성 + booster.predict(절단)"로 잡는 것은 GRU와 동형 경계 — 비용차(GRU 고정 O(F) vs XGB 8행 7종 통계)는 측정 대상 그 자체이지 경계 비대칭 아님. **PASS.**
- **mn-R3-1 (제목) → 해소됨** [확인됨: `decisions.md:1`].

### PASS (라운드 4에서 재확인)

- v4 코드 인용 [확인됨] 대부분 정합: `train/tree.py:69-75`(절단식), `eval/crosssite.py:60,65`(주입), `h2b_train_trees.py:183`(best_iter를 metric으로만 로깅)·`:189`(preprocess.json = featureset/scale_pos_weight/tau/hp/note, best_iter 없음)·`:99`(네이티브 부스터 저장), `h3b_crosssite.py:144`(run metric 조회), `predictor.py:44`, `.ubj` 실재·`deploy/artifacts/` GRU 별칭만 — 전부 대조 통과.
- best_iter 의존(트리 절단)과 NB2 lookback 버퍼(입력 재구성)는 직교 독립 의존으로 모순 없이 공존 [확인됨: `decisions.md:58,83`].
- PASS 게이트 2 개정이 결정 3과 일치, 다른 게이트와 모순 없음.

### blocker

#### B-R4-1. best_iter의 지목 소스(run metric)가 저장소에 영속돼 있지 않고, ".ubj에도 없다 [확인됨]"이 미검증·모순 — 챔피언 재구성 입력의 영속 흐름 단절
- **항목**: 결정 2 "★XGB 아티팩트·전처리 소스 식별" 3항(`decisions.md:58`) · PASS 게이트 1(`:141`)
- **문제**: v4는 best_iter 소스를 "run metric `metrics.best_iter` — `.ubj`에도 `preprocess.json`에도 **없다**"로 [확인됨] 단정하고 이를 XGB 서빙의 유일 조회 소스로 못박았다. 그러나 (1) 이 저장소 mlruns run 디렉토리는 `artifacts/`만 존재, `metrics/`·`meta.yaml` 부재 → 지목한 metric이 **영속 안 됨**. (2) `.ubj` 두 파일 모두 `best_iteration`을 **임베드** → ".ubj에도 없다 [확인됨]"은 미검증·사실 반대.
- **근거**:
  - run metric 부재: `mlruns/1/3e21f380…/` 하위 = `.ubj` + `preprocess.json` 2개뿐 [확인됨: Glob]. `metrics/`·`meta.yaml` 없음, `grep best_iter mlruns/` = 0건 [확인됨].
  - h3b 자신도 이 metric 스토어에 의존(`h3b_crosssite.py:139` search_runs → `:144` `r.get("metrics.best_iter")`) — H3 실행 당시엔 full 스토어, 저장소엔 artifacts-only로 pruned. 지금 재현 불가.
  - `.ubj` 임베드: `h2b_train_trees.py:99` 네이티브 부스터 저장 → `grep best_iteration …/xgboost_vitals.ubj` = 1건, `…_labs.ubj` = 1건 [확인됨]. XGBoost 네이티브 저장은 best_iteration을 learner attribute로 보존 → **.ubj에서 복구 가능**.
  - 다른 영속 소스 없음: 비-mlruns에서 best_iter는 코드 파일에만, 값 담은 산출물 없음 [확인됨].
- **왜 blocker**: (1) **거짓/미검증 [확인됨] 재발** — ".ubj에도 없다" 근거가 .ubj를 검사 안 했고 실제론 임베드. 라운드 1 B1과 동형. (2) **영속 흐름 단절** — 챔피언 재구성의 load-bearing 입력 best_iter가 지목 소스(run metric)에 없어, 핸드오프가 설계대로 "run metric 조회"를 구현하면 이 저장소에서 아무 값도 못 얻음. 값이 있는 유일 장소(.ubj attribute)를 "없다"고 오배제. (3) 구현 디테일 아닌 **소스 식별**(설계부 몫) — B-R3-1이 고친다던 부분이 여전히 틀림.
- **제안**: 결정 2 3항·게이트 1 소스 서술 정정. (a) `.ubj` 임베드 `best_iteration` attribute를 1차 소스로 확정(booster에서 직접 복구, run 스토어 불필요)하거나, (b) run metric 유지 시 커밋된 mlruns가 artifacts-only로 pruned되어 `metrics.best_iter`가 현재 저장소에 없음을 명시하고 복구 경로(.ubj attr vs H2 재실행 vs 스토어 복원)를 의존으로 식별. 어느 쪽이든 미검증 "`.ubj`에도 없다 [확인됨]"은 삭제/교체.

### major
- 없음. (M-R3-1 해소됨.)

### minor
- **mn-R4-1.** `tree.py:74` 절단은 `if best_iter and best_iter >= 0`일 때만, falsy/음수면 `None`(전체 트리) 폴백. 챔피언 best_iter는 양수라 실무 영향 없으나, B-R4-1 정정 시 "주입 best_iter가 유효 양수임"을 서빙 계약에 명시하면 무성 전체-트리 추론 방지.

### 판정

**라운드 4 blocker 1건(B-R4-1) → HOLD.**
- M-R3-1(major)·B-R3-1 의존 식별부 = 해소됨.
- 그러나 B-R3-1 **소스 식별부**가 미해소 — 지목 소스(run metric)가 저장소에 영속 안 됨 + ".ubj에도 없다 [확인됨]"이 .ubj 임베드 best_iteration과 모순. B-R3-1 보완이 만든 새 결함(B-R4-1).
- **blocker 0 아님 → PASS 불가.** 사람 재에스컬레이션.
