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
