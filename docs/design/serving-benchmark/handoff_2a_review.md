# Serving-Benchmark 핸드오프 검토 (2A — 관측성 env 게이트)

- **대상**: `docs/design/serving-benchmark/handoff_2a.md` (arm-2 토글, 명세부 v1)
- **선행**: decisions.md v5 결정 1 격리 예외 + 결정 4 계측 대칭성 + NB3.
- **핵심 질문**: 기계적 실행 / 성공기준 블랙박스 RED 번역 가능 / 출제자(§A)-응시자(§B) 분리.

---

## 라운드 1

- 대상 commit: 작업트리 (초안)
- 검토일: 2026-07-02
- 판정: **HOLD — blocker 2건** (major 1, minor 2)

### PASS

- **§B1 앵커 라인 정확** — `metrics.py:52-56`(피처 루프), `:45-46`(`PREDICT_REQUESTS.inc`·`LATENCY.observe`), `app.py:96-98`(predict+record), `:102`(`get_window().add`), `:103-104`(응답). 가드 대상 2곳·불변 대상이 코드와 1:1. [확인됨]
- **B1 latency 함정 안내 정확** — `metrics.record`가 `LATENCY.observe`와 피처 루프를 함께 함 → "record 전체 감싸면 latency 죽음, 피처 루프만 조건화" 경고 타당. [확인됨: `metrics.py:43-56`]
- **A2가 진짜 위험 잡고 구조상 nil** — 게이트가 끄는 두 지점(피처 루프·window.add) 어느 것도 predict 입력 아님(`predictor.py:41-51`은 `self._h`/`self.pre`만, window는 별도 store `window.py:19-24`). ON/OFF 응답 바이트 동일 비교 유효. [확인됨]
- **A3가 B1 함정의 블랙박스 감시자** — `LATENCY`·`PREDICT_REQUESTS`는 게이트 밖(무라벨이라 `_count`/`_total` 항상 노출). record 전체 오감싸면 count 미증가로 A3가 RED. [확인됨]
- **A4-a 기본값 ON = 회귀 없음, §B가 역전 명시** — 현재 record 무조건 루프가 기준선, §B가 "미설정→ON(기존 옵트인은 미설정→OFF)" 차이 명문화. [확인됨: `metrics.py:38-40`]

### blocker

#### B-1. §A가 게이트 env 이름을 감춰 A1-a(핵심 RED) 기계 작성 불가 — 통합 데드락
- **문제**: A0(`handoff_2a:24`)가 "정확한 env 이름은 §B가 정한다"며 은닉. spec-writer가 OFF 서버 기동하려면 `monkeypatch.setenv(<이름>, "0")`이 필요한데 이름을 모르면 테스트 미완성. 추측한 이름(`FOO=0`)은 실제 게이트(`SEPSIS_SERVE_AUX_METRICS`, `§B79`)가 안 읽어 **서버 ON 동작** → A1-a가 "계열 부재" 기대하나 계열 존재 → GREEN 후에도 **영원히 통과 불가**.
- **근거**: 형제 핸드오프는 운영 env를 §A에 노출(선례) — `handoff.md:83` "`SEPSIS_XGB_BEST_ITER_OVERRIDE`가 있다"(§A3-b). env 이름은 문서화된 운영 인터페이스(블랙박스 입력 측 계약)라 §A 노출이 정합. 출력 측 계열 이름(`serve_input_feature_value`)은 §A에 노출하며 입력 측만 감추는 건 비대칭. [확인됨: `handoff.md:83`, `handoff_2a:20-24`]
- **제안**: A0에 정확한 이름 명시(예: "`SEPSIS_SERVE_AUX_METRICS`: 미설정/`1/true/on`→ON, `0/false/off`→OFF"), 또는 명시적 테스트 seam 정의. §A·§B가 동일 이름 공유를 문서상 보장.

> **[reviser 응답]** 해소: §A A0(handoff_2a:18-24)에 정확한 env 이름 `SEPSIS_SERVE_AUX_METRICS`를 명시하고 해석표(미설정/`1/true/on`→ON, `0/false/off`→OFF)를 노출. spec-writer 주의에 `monkeypatch.setenv("SEPSIS_SERVE_AUX_METRICS", "0")` 사용 예시 추가. "이 env 이름은 §B 구현이 읽는 이름과 동일함을 문서상 보장"을 명문화(§B2·B79 동일 이름 확인). 형제 `handoff.md:83`이 운영 env를 §A에 노출한 선례로 정합성 근거 명기. 출력 측 계열 이름만 노출하고 입력 측을 감추던 비대칭 해소.

#### B-2. A1-a "나타나지 않는다"가 프로메테우스 동작과 어긋나 잘못된 RED 유도
- **문제**: `INPUT_FEATURE`/`INPUT_MISSING`은 `metrics.py:23-27`에서 **import 시 REGISTRY 등록** → 게이트 OFF여도 `/metrics`에 `# HELP`·`# TYPE` **헤더는 항상** 노출. OFF에서 사라지는 건 라벨 있는 **샘플 라인**(`serve_input_feature_value_bucket{feature=...}`/`_count`/`_sum`, `serve_input_missing_total{feature=...}`)뿐. 순진한 테스트(`assert "serve_input_feature_value" not in text`)는 헤더 때문에 OFF에서도 실패.
- **근거**: `metrics.py:23-27` 모듈 레벨 생성. prometheus_client는 HELP/TYPE 항상, 샘플은 관측된 라벨 조합만. [확인됨]
- **제안**: A1-a를 "피처별 **샘플 라인**이 OFF에서 한 줄도 안 나타남(HELP/TYPE 헤더만 남음), ON에선 나타나고 증가"로 조인다. "substring 부재"가 아니라 "라벨 샘플 라인 부재/불변".

> **[reviser 응답]** 해소: A1(handoff_2a:26-33) 재작성. (1) 프로메테우스 노출 방식 주의 블록 추가 — "`# HELP`·`# TYPE` 헤더는 등록만 돼 있으면 OFF여도 항상 노출, 사라지는 건 라벨 샘플 라인뿐, 순진한 `assert "..." not in text`는 헤더 때문에 OFF에서도 실패". (2) A1-a를 `feature=...` 라벨 샘플 라인(`serve_input_feature_value_bucket/_count/_sum{feature=...}`, `serve_input_missing_total{feature=...}`)의 **0줄 부재**로 조임(HELP/TYPE 헤더만 잔존). (3) A1-b를 라벨 샘플 라인 출현 + `_count{feature=...}` 호출 횟수만큼 증가로 조임. "substring 부재"→"라벨 샘플 라인 부재/불변"으로 전환. [확인됨: `metrics.py:23-27` 모듈 레벨 등록 확인]

### major

#### M-1. §A에 XGB 서빙의 게이트 준수 검증 기준 부재 — 결정 4 대칭성의 절반 미검증 + 선행 전제 누락
- **문제**: 이 핸드오프 존재 이유 = 결정 4 계측 대칭성(arm을 **두 서빙 모두** 대칭). §B81은 "GRU·XGB 양쪽 동일 스위치" 요구하나 §A 성공기준은 "한 서버"만 시험 → main이 GRU에만 게이트 달아도 통과, 대칭 조용히 깨짐. XGB 최소 서빙 앱은 **아직 코드에 없고**(`handoff.md`가 생성, 게이트는 2차로 미룸 `handoff.md:102`), 2A가 그 앱에 게이트 다는 주체인데 "handoff.md(1차) XGB 앱 GREEN 완료" 선행 전제 미명시.
- **근거**: decisions.md:103-104(양쪽 대칭 필수), handoff.md:102, §A에 XGB 특정 기준 부재, `serve/`에 xgb 앱 없음. [확인됨]
- **제안**: (1) §A에 "동일 성공기준이 GRU·XGB 두 인스턴스 모두 성립" 대칭 기준 명시(또는 2A를 GRU 한정 + XGB 게이트 별도 명기). (2) 헤더 전제에 "handoff.md(1차) XGB 최소 서빙 GREEN"을 선행 의존으로.

> **[reviser 응답]** 해소: (1) §A에 신규 성공기준 **A0-대칭**(handoff_2a:30-34 부근) 추가 — "A1~A4 모든 성공기준이 GRU 서빙·XGB 최소 서빙 두 인스턴스 각각에서 독립 성립, 한 서버만 통과하면 실패", spec-writer에게 A1~A4를 두 인스턴스로 파라미터화/복제하라 명시. 두 서버의 응답 스키마·피처 개수 차이는 인정하되 게이트 관측 계약은 동형임을 명기. (2) 헤더 **선행 의존** 블록 추가(handoff_2a:4) — "`handoff.md`(1차) XGB 최소 서빙 구현 완료(GREEN)"를 선행 전제로, `handoff.md:102`가 게이트를 1차 범위 밖으로 미뤘음을 근거로 2A가 게이트 주체임을 확정. [확인됨: `handoff.md:102`]

### minor

- **m-1. §B81 "handoff_2a 1차에서 생성된 앱"은 오기** — XGB 앱은 `handoff.md`(1차)가 생성. 참조를 "handoff.md(1차)"로 정정. [확인됨: `handoff.md:1`, `handoff_2a:81`]

  > **[reviser 응답]** 해소: §B2(handoff_2a:81)를 "`handoff.md`(1차)가 생성한 앱 — 선행 의존, 헤더 전제 참조"로 정정.

- **m-2. A2-b가 §A에 내부 아키텍처 용어("GRU hidden state / XGB lookback 버퍼") 노출** — 하드 누수는 아니나 순수 행동 서술("환자별 상태가 시퀀스에 걸쳐 누적")로 대체 권고(형제 `handoff.md:41` 수위). [handoff_2a:37]

  > **[reviser 응답]** 해소: A2-b(handoff_2a:37)의 "GRU hidden state / XGB lookback 버퍼"를 순수 행동 서술("같은 환자의 여러 타임스텝을 순서대로 보내 환자별 상태가 시퀀스에 걸쳐 누적되게 한 뒤")로 대체. 내부 아키텍처 용어 제거, 형제 `handoff.md:41` 수위와 정합.

### 판정

**blocker 2건 → HOLD.** B-1(env 이름 은닉→통과 불가)·B-2(A1-a 프로메테우스 부정합) 해소돼야 spec-writer가 A1 핵심 RED를 기계 작성 가능. major M-1(XGB 대칭 미검증·선행 전제)도 함께 보완 권고.
