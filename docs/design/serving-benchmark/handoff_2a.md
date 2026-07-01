# Serving-Benchmark 구현 핸드오프 (명세부) — 2A: 관측성 env 게이트 (arm-2 토글)

> **전제**: `docs/design/serving-benchmark/decisions.md`(설계부) v5, 5라운드 검토 통과(blocker 0). 본 문서는 그 **결정 1 격리 예외 + 결정 4 계측 대칭성(arm-2 토글) + NB3**만 자립형으로 명세한다. 계측 수집·실험 프로토콜·비용은 **handoff_2b**(벤치 하니스)로 분리한다.
> **워크플로우**: 검토(`handoff_2a_review.md`) 통과 → spec-writer가 §A만 보고 TDD(RED) → main이 §A+§B로 구현(GREEN). 푸시는 사람 게이트(자동 금지).
> **출제자-응시자 분리**: §A(계약·성공기준·실패모드)는 **spec-writer 전용** — src 라인 참조 없이 관측 가능한 행동으로만. §B(구현 참조)는 **main 전용**.
> **상태**: 명세부 v1 — 레드팀 검토 전.

## 0. 한 줄 요약

벤치의 "순수 추론 프로파일"(arm-2)을 재려면, 서빙이 매 `/predict`마다 하는 **부가 계측**(피처별 입력분포 히스토그램 + 드리프트 윈도우 적재)을 **끌 수 있어야** 한다. 이를 위해 **관측성 전용 env 게이트** 하나를 GRU·XGB 서빙 양쪽에 심는다. 철칙: **이 게이트는 "무엇을 관측하는가"만 켜고 끈다 — "무엇을 예측하는가"(predict·응답·서버 latency 히스토그램)는 절대 안 바꾼다.** 이것이 결정 1 격리 원칙의 유일한 예외(관측성 한정)다.

---

# §A. spec-writer 전용 — 계약·성공기준·실패모드

> spec-writer는 이 절만 읽고 TDD 테스트를 작성한다. 전부 **관측 가능한 입출력**으로 검증한다 — HTTP 요청/응답 바이트, `/metrics` 텍스트에 나타나는 시계열 이름과 카운터 값, 그리고 **문서화된 운영 env 스위치**를 통한 관측이다. 내부 구현(어느 파일·함수·라인)은 알 필요 없다.

## A0. 게이트 스위치 (관측 인터페이스)

서빙에는 **부가 계측 on/off를 정하는 문서화된 운영 env 스위치**가 있다(이름은 §B에서 확정하되, spec-writer는 "그 스위치" 존재를 전제로 두 상태를 시험한다). 두 상태:
- **ON (기본값 = 배포 프로파일 arm-1)**: 서빙이 부가 계측을 전부 수행한다.
- **OFF (순수 추론 프로파일 arm-2)**: 부가 계측을 하지 않는다.

> spec-writer 주의: 스위치의 **정확한 env 이름은 §B가 정한다.** 테스트는 "부가계측 ON으로 기동한 서버"와 "OFF로 기동한 서버" 두 인스턴스의 관측 차이로 성공기준을 검증한다(같은 프로세스에서 런타임 토글이 필요하진 않다 — 기동 시점 결정으로 충분).

## A1. 게이트 OFF → 부가 계측 시계열이 사라진다 (arm-2)

부가 계측 스위치를 **OFF로 기동**한 서버에 `/predict`를 여러 번(예: 20회) 보낸 뒤 `/metrics`를 읽으면:

- **성공기준 A1-a**: 피처별 입력 분포 시계열 `serve_input_feature_value*`와 결측 카운터 `serve_input_missing_total*`가 **나타나지 않는다**(또는 값이 0에서 안 늘어난다). 즉 `/predict`를 아무리 호출해도 이 두 계열의 관측이 발생하지 않는다.
- **성공기준 A1-b (대조)**: 같은 서버를 **ON으로 기동**하고 같은 요청을 보내면 위 두 계열이 `/metrics`에 **나타나고 값이 증가**한다.
- 두 상태의 이 차이가 "게이트가 실제로 부가 계측을 끈다"를 관측 증명한다.

## A2. 게이트는 예측/응답을 바꾸지 않는다 (격리 — load-bearing)

- **성공기준 A2-a (응답 불변)**: 같은 `/predict` 요청을 **ON 서버**와 **OFF 서버**에 각각 보내면, 응답 JSON `{patient_id, p, alarm, featureset}` 네 키의 값이 **완전히 동일**하다(p는 부동소수 동일, alarm·featureset 동일). 게이트는 관측만 바꾸지 예측을 안 바꾼다.
- **성공기준 A2-b (상태 진행 불변)**: 환자별 상태(GRU hidden state / XGB lookback 버퍼)를 쌓는 시퀀스를 ON·OFF 서버에 동일하게 흘려도, 매 응답 p 시퀀스가 두 상태에서 동일하다. 게이트가 예측에 쓰는 상태 진행을 건드리지 않는다.

> **근거**: arm-1(계측 ON) vs arm-2(계측 OFF)의 latency·메모리 차이를 "부가 계측 세금"으로 정직하게 귀인하려면, 두 arm이 **예측 경로는 한 글자도 다르지 않아야** 한다. 예측이 갈리면 두 arm의 차이에 예측 차이가 섞여 귀인이 오염된다.

## A3. 서버 내부 latency 히스토그램은 게이트와 무관하게 유지된다

- **성공기준 A3**: 부가 계측 스위치가 **OFF**여도, `/predict`를 **N회** 호출하면 서버 latency 지표 `serve_predict_latency_seconds_count`가 **정확히 N 증가**한다. 즉 arm-2(순수 추론)에서도 서버 내부 추론 latency 관측은 살아 있다 — 이건 "부가 계측"이 아니라 벤치의 핵심 지표이기 때문이다.
- 요청 카운터 `serve_predict_requests_total`도 게이트와 무관하게 호출당 1 증가한다.

## A4. 실패/기본 모드

- **성공기준 A4-a (안전한 기본값)**: env 스위치를 **설정하지 않고** 기동하면 서버는 **ON(배포 프로파일)**으로 동작한다 — 프로덕션·데모는 계측을 켠 채 도는 것이 정상이고, 끄는 것은 벤치가 명시적으로 opt-in 할 때뿐이다. (설정 안 함 = 프로덕션 회귀 없음.)
- **성공기준 A4-b (알 수 없는 값 관대)**: 스위치에 예상 밖 문자열을 줘도 500으로 죽지 않는다(기존 옵트인 스위치와 동일한 관대한 파싱 — 참/거짓으로만 해석).

## A5. 범위 밖 (이 핸드오프에서 만들지 않음)

- client 벽시계 계측·network 잔차 분해·throughput·메모리 측정 — **handoff_2b**.
- arm-1/arm-2를 실제로 **돌려서 수집·비교**하는 벤치 러너 — handoff_2b(이 핸드오프는 "끌 수 있게"까지만).
- 비용표 — handoff_2b.
- 예측 로직·모델·featureset 변경 — 하지 않음.

---

# §B. main 전용 — 구현 참조

> **spec-writer는 이 절을 읽지 않는다.** §A 성공기준을 통과시키기 위한 구현 앵커. 경로·라인은 설계부 v5에서 `[확인됨]`으로 검증된 것.

## B1. 게이트가 가드할 정확한 두 지점 (그 외 불변)

부가 계측 = 다음 **둘뿐**이며, 게이트 OFF일 때 이 둘만 건너뛴다:
1. **피처별 입력 분포 루프**: `src/sepsis/serve/metrics.py:52-56` — `for name, v in zip(feature_names, raw_row): INPUT_MISSING.labels().inc() / INPUT_FEATURE.labels().observe()`. (GRU 9회 / XGB 18회 루프.)
2. **드리프트 윈도우 적재**: `src/sepsis/serve/app.py:102` — `get_window().add(req.patient_id, row)`.

**절대 건드리지 않는 것**(게이트와 무관하게 항상 실행):
- `metrics.py:45-46` `PREDICT_REQUESTS.inc()` · `LATENCY.observe(latency_s)` — 요청 카운터·서버 latency 히스토그램(A3). `PRED_PROB.observe`(:47)도 유지 권장(예측 분포는 부가계측 아님) — 단 이건 §A 성공기준 아님, main 판단.
- `app.py:96-98` `t0=perf_counter(); out=predict(...); metrics.record(...)`의 **predict 호출과 latency 관측** — A2·A3.
- `app.py:103-104` 응답 dict — A2-a.

> 주의: `metrics.record(...)`(app.py:98)는 한 함수 안에서 `LATENCY.observe`(유지)와 피처 루프(가드 대상)를 **함께** 한다. 따라서 게이트를 `metrics.record` 호출 전체에 걸면 안 된다 — latency 관측까지 죽는다. 게이트는 **피처 루프(metrics.py:52-56)만** 감싸야 한다(예: `record`에 `aux: bool` 인자를 추가해 루프만 조건화, 또는 루프를 별도 함수로 분리해 조건 호출). `get_window().add`(app.py:102)는 호출 지점에서 조건화.

## B2. env 스위치 (기존 옵트인 패턴의 확장)

- 이름 제안: `SEPSIS_SERVE_AUX_METRICS`(기본 ON). 호출 시점 동적 판독(import 상수화 금지) — 기존 `_per_patient_enabled()`(`metrics.py:38-40`, `SERVE_PER_PATIENT_GAUGE`)와 **동형 패턴**. 관대한 파싱(`1/true/yes/on` → 켬; 그 외/미설정 처리는 아래).
- **기본값 = ON**: `_per_patient_enabled()`는 미설정 시 OFF(옵트인)지만, 이 스위치는 미설정 시 **ON**(A4-a — 프로덕션은 계측 켠 채가 정상, 끄는 게 opt-in). 즉 "명시적으로 `0/false/off`일 때만 OFF". 관대한 파싱으로 알 수 없는 값은 ON 유지(A4-b, 500 금지).
- GRU 서빙(`app.py` 재사용)과 XGB 최소 서빙(handoff_2a 1차에서 생성된 앱) **양쪽에 동일 스위치·동일 의미**로 심는다.

## B3. 격리 예외 준수 (PASS 게이트 1 — grep 강제)

- 결정 1 격리 원칙 = **"예측/추론 로직 불변, 관측성은 env-게이트로 가감 가능"**(NB3). 이 게이트가 만지는 건 관측성 부속(피처 루프·window add)뿐이다.
- **grep 증명 대상**: `predict()`·`_row_from`·응답 dict(`app.py:103-104`)·`LATENCY`(`serve_predict_latency_seconds`) 관측 지점이 **미변경**임을 PR에서 grep으로 보인다. 게이트 추가는 이 지점들 밖에서만.
- `PRED_PROB_LATEST` gauge의 기존 `SERVE_PER_PATIENT_GAUGE` 가드는 그대로 둔다(별개 옵트인).

## B4. 왜 런타임 토글이 아니라 기동 시점으로 충분한가

- 벤치는 arm-1 측정 → 서버 재기동 → arm-2 측정으로 **순차** 진행(결정 4 순차 실행). 한 프로세스에서 런타임에 껐다 켤 필요 없음. 기동 시 env 판독으로 충분(단 `_per_patient_enabled`처럼 호출 시점 판독이면 테스트가 monkeypatch로 한 프로세스에서 두 상태를 볼 수도 있어 편의상 이득).

---

## 핸드오프 검토 요청 항목 (redteam이 팔 자리)

1. A1(부가계측 OFF에서 시계열 사라짐)이 spec-writer에게 **블랙박스로 관측 가능**한가 — `/metrics` 텍스트에서 `serve_input_feature_value`·`serve_input_missing_total`의 유무/증가를 실제로 볼 수 있는가(프로메테우스가 라벨 없는 계열을 어떻게 노출하는지 고려).
2. A2(예측/응답 불변)가 게이트의 진짜 위험(예측 오염)을 잡는가 — ON/OFF 응답 바이트 동일 비교로 충분한가.
3. B1의 "게이트를 `metrics.record` 전체에 걸면 latency까지 죽는다"는 함정을 §B가 정확히 피하게 안내하는가 — 피처 루프만 가드하는 설계가 실제 코드 구조와 맞는가.
4. A4-a 기본값 ON이 프로덕션 회귀 없음(설정 안 하면 기존과 동일)을 보장하는가.
5. 격리 예외 grep 증명(B3)이 실제로 predict/응답/LATENCY 불변을 강제 가능한가.
