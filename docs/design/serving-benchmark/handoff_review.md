# Serving-Benchmark 핸드오프 검토 (핸드오프부)

- **대상**: `docs/design/serving-benchmark/handoff.md` (명세부 v1 — 1차 XGB 최소 서빙)
- **선행**: `decisions.md` v5 (review-loop 5라운드 blocker 0 통과) 중 결정 2 + PASS 게이트 1만 1차 명세. 계측·비용·관측성 게이트는 2차 핸드오프로 분리.
- **핵심 질문**: 기계적 실행 가능 / 성공기준 블랙박스 검증 가능 / 출제자-응시자(§A vs §B) 분리 준수

---

## 라운드 1

- 대상 commit: HEAD `72808d7` 기준 작업트리
- 검토일: 2026-07-01
- 판정: **HOLD — blocker 3건**

공통 뿌리: §A 도입부(`handoff.md:16`) "§A는 전부 관측 가능한 입출력"이라는 단언과 달리, **A3·A4의 load-bearing 성공기준이 화이트박스 속성**(챔피언 확률 재현·내부 타이머 경계)이다. spec-writer가 이를 RED 테스트로 번역할 수단이 §A에 없다. 사용자 최우선 우려였던 §A2-c 패딩·clip skew는 코드 대조 결과 **정합(PASS)**.

### PASS

- **XGB 입력 전처리 train-serve 정합 (skew 없음)** — 트리 학습 입력은 raw에 `lookback_summary`만, clip·ffill·z-score 없음 [확인됨: `eval/crosssite.py:42-43` `_tree_summary(raw)=lookback_summary(raw) # NaN-native, no stats`, `h2b_train_trees.py:60`, `build_dataset.py:163`]. GRU 경로만 clip(`crosssite.py:46-50`, `preprocess_rt.py:45-48`). B2(`handoff.md:90`)가 "요청 raw 1행→버퍼 push→lookback_summary, 0-fill 금지"로 정확히 raw 경로 지시. **PASS.**
- **버퍼 부족분 NaN-aware 패딩 = 학습 동일** — `features.py:21-27` `_windows`가 앞쪽 `np.full((window-1,F),nan)` 좌측 패드. 버퍼 N행(≤8)에 lookback_summary 후 마지막 행 = `(8-N)` NaN + N 실측 = 학습 t번째 윈도우와 동일. B2가 "동일 함수 재사용+마지막 행" 지시. **PASS.**
- **best_iter 소스·절단 정합** — B1(`handoff.md:84`) `.ubj` 임베드 `b.best_iteration`=105/149; B3(`handoff.md:94`) `tree.py:69-75` `iteration_range=(0,int(best_iter)+1)` 재사용. decisions.md v5 결정 2-3항 정합. **PASS(소스·경로 한정 — 검증수단은 B1 참조).**
- **/predict 계약 4키 복제** — B4(`handoff.md:100`)·A1의 `{patient_id,p,alarm,featureset}`가 `app.py:103-104` 일치, `alarm=p>=tau`. **PASS.**
- **A2-b(환자 격리) 블랙박스 검증 가능** — "P 단독" vs "P·Q 교차" 시 P의 p 시퀀스 동일성으로 관측 검증. **PASS.**
- **범위 분리 정직성** — 계측·비용·관측성 게이트를 2차로 명시 분리(`handoff.md:3,67-72`). **PASS.**
- **§A 리터럴 src 누수 없음** — §A(14-72)에 파일·함수 리터럴 없음(개념 누수는 minor). **PASS(리터럴 한정).**

### blocker

#### B1 — §A3(best_iter 절단)이 블랙박스로 검증 불가: 골든 참조·주입 수단 부재
- **문제**: A3-a(`handoff.md:57`)는 "절단 예측 ≠ 전체 트리 예측"이면 통과라 하나, 서버 `/predict`는 **절단된 확률 하나만** 반환. spec-writer가 "전체 트리 예측"을 얻으려면 `.ubj`를 직접 로드+63/126차원 요약 재구성해야 → 구현 지식 유입, 블랙박스 아님. **챔피언 골든 기대값이 §A에 없어** "절단된 챔피언" vs "전체 트리(오모델)"를 구분하는 RED 테스트를 못 쓴다.
- **근거**: 서버 출력=절단 확률뿐; 참조 오라클은 `crosssite.score_tree_frozen`(`crosssite.py:60-65`)=src. §A(14-72)에 골든 확률 없음. `handoff.md:16` 단언과 모순.
- **제안**: 지정 입력 시퀀스의 **챔피언 골든 확률**(H3 평가 산출 관측 기대값)을 §A에 수치로 박아라(예: "시퀀스 S → `p ≈ 0.xxxx ± eps`"). 골든은 **출제자(핸드오프)**가 제공(순환 테스트 회피). 못 주면 A3-a를 §A에서 내리고 §B main-소유 characterization 테스트로 재배치.

> **[reviser 응답]** 해소(옵션 i 택): §A3에 **골든 시퀀스 `S_vitals`(9키 5행)를 관측 request payload로 명시**하고, 마지막 응답 기대값을 실측 상수로 박음 — `p=0.70981 ± 1e-4, alarm=true`(handoff.md §A3-a). 실제 `.ubj`+`lookback_summary`+best_iter=105 절단으로 산출 [확인됨: xgboost 3.3.0, `.venv/bin/python` 실측]. 감별력도 관측화: 비절단(전체 트리) 서버는 같은 시퀀스에 `≈0.69233` 반환(gap 0.0175 ≫ eps) → "절단이 걸렸음"을 서버 출력만으로 증명. 산출 코드경로는 §B3.1에만, §A엔 상수만(출제자-응시자 분리). vitals_labs 골든(`0.83356`)도 §A3에 선택 제공. **A3-a는 이제 서버가 반환하지 않는 "전체 트리 예측"에 의존하지 않음.**

#### B2 — §A3-b(무성 폴백 금지) 실패 경로가 트리거 불가능한 죽은 계약
- **문제**: A3-b(`handoff.md:58`)는 "best_iter 유효 양수 아니면 명시적 실패, 무성 전체-트리 폴백 금지"를 요구. 그러나 실제 `.ubj`는 항상 105/149(양수)라, 이 실패 경로를 **발동시킬 수단이 §A·§B 어디에도 없다**(무효 best_iter 주입 픽스처·config·env 부재). 발동 불가 계약은 검증 불가.
- **근거**: `tree.py:74` `rng=... if best_iter and best_iter>=0 else None`. `handoff.md:95`가 폴백 금지하나 임베드값 상수라 무효화 경로 미노출.
- **제안**: 테스트 훅 명세 — (a) best_iteration 없는/무효 **크래프트 `.ubj` 픽스처**, 또는 (b) best_iter 오버라이드 config/env. 그래야 "명시적 실패"가 기동 실패/에러 응답으로 관측 검증.

> **[reviser 응답]** 해소(옵션 b 택): §A3-b에 **문서화된 운영 오버라이드 env `SEPSIS_XGB_BEST_ITER_OVERRIDE`를 관측 계약으로 선언** — 무효값(`0`·음수·`none`) 설정 후 기동 시 "기동 실패 또는 `/predict` 5xx/에러", 200+전체트리 무성 폴백은 FAIL(handoff.md §A3-b). env 스위치라 spec-writer가 §B 안 보고도 실패경로를 **발동 가능**. 미설정=정상(A3-a 골든 재현). 훅 배선(오버라이드 주입구 + 유효성 게이트로 `tree.py:74` else-None 경로 차단)은 §B3.2에 앵커. 크래프트 `.ubj` 대안도 §B3.2에 병기. **죽은 계약 → 발동·관측 가능한 계약으로 전환.**

#### B3 — §A4(latency 관측 경계 "재구성 포함")이 블랙박스 검증 불가 + §A로 내부 경계 누수
- **문제**: A4(`handoff.md:62-64`)는 히스토그램 구간이 "버퍼→요약 재구성+추론"을 함께 감싼다는 **화이트박스 코드 경계**를 §A 성공기준으로 둔다. 외부는 latency 수치만 보여 "타이머가 재구성을 감쌌는지" 관측 증명 불가. 스스로 "검증 방법은 spec-writer 재량이나…권장"(`handoff.md:64`) punting — 재량·권장은 성공기준 아님(RED 불가). 동시에 내부 구현 구조를 spec-writer에 노출(출제자-응시자 위반).
- **근거**: 지표=`serve_predict_latency_seconds`(`metrics.py:18,46`) — 외부 관측은 집계 latency뿐. 요구 경계는 §B5(`handoff.md:106`)에 이미 구현 앵커로 존재.
- **제안**: 경계-포함 요구는 §B 전용으로(B5에 이미 있음). §A4를 남긴다면 **관측 가능 기준**으로만 재명세 — 예 "/predict 1회마다 `serve_predict_latency_seconds_count`가 정확히 1 증가". 내부 타이머 경계 서술은 §A에서 삭제.

> **[reviser 응답]** 해소: §A4를 **관측 기준 2개로 재명세** — A4-a(`/metrics`에 `serve_predict_latency_seconds_count`·`_bucket`·`_sum` 존재), A4-b(`/predict` N회 → count 정확히 N 증가)(handoff.md §A4). **"버퍼→요약 재구성 포함" 내부 타이머 경계 서술을 §A에서 삭제**하고 §B5로 이관(경계 소유권 문단 추가, main이 코드로 보장·PR 육안 확인). §0 요약의 화이트박스 힌트("버퍼 재구성+추론 함께 감싸")도 "같은 범위를 재도록 대칭"으로 순화(§B5 포인터만). 재량·권장 punting 제거.

### major

- **M1 — §B: per-patient 버퍼 스레드 안전성·replicas=1 실패모드 누락**: throughput은 `replay_many` ThreadPoolExecutor 동시 부하. GRU는 환자별 lock+"replicas=1 assumed, Redis for >1"(`predictor.py:5-7,29-34,43`) 명시. XGB 버퍼도 동시 read-modify-write 겹치면 A2-b 격리 깨질 수 있으나 §B2(`handoff.md:90`)는 "재량"으로만. **제안**: §B에 "버퍼는 환자별 lock 직렬화(GRU 패턴), replicas=1 가정"을 실패모드로 명시.

> **[reviser 응답]** 해소: §B2에 스레드 안전성·실패모드 문단 추가 — **환자별 lock 직렬화**(GRU `predictor.py:5-7,29-34,43` `_lock(pid)` 패턴 명시 참조), **replicas=1 가정**, replicas>1 시 공유 스토어(Redis) 필요를 실패모드로 명시(handoff.md §B2).

- **M2 — §A2-a "다를 수 있다(may differ)"는 강제 어서션 불가**: A2-a(`handoff.md:45`) "두 응답 p가 다를 수 있다"는 테스트가 강제 못 함. **제안**: "예시 행 X_1..X_k에 대해 첫 p와 이력 후 p가 **반드시 다르다**"로 조이고 요약이 확실히 변하는 구체 예시 행을 §A에 제공.

> **[reviser 응답]** 해소: A2-a를 **"반드시 다르다"로 조이고 구체 수치 제공** — 골든 마지막 행 X를 (i) 첫 요청 단독 `p≈0.64867`, (ii) `S_vitals` 이력 후 `p≈0.70981`(gap 0.061 ≫ eps), 같으면 FAIL(handoff.md §A2-a). 두 값 모두 실측 상수. A2-b도 골든에 묶어 구체화(P에 `S_vitals`+Q 교차→P 5번째=0.70981).

### minor

- **mn1 §A 개념 누수(리터럴 아님)**: A2가 7종 요약통계·`LOOKBACK=8`("8행")·"NaN-aware 패딩", A3가 "best_iteration/전체 트리 절단"을 spec-writer에 노출. 블랙박스 상태 테스트만 남기고 통계 목록·상수 트림 권장.

> **[reviser 응답]** 부분 해소: A3에서 "best_iteration/전체 트리" 서술을 **골든 상수 대조 + 감별력(비절단 시 다른 수치)** 로 대체해 개념 의존을 낮춤. A2-d의 "요약통계 7종" 서술은 근거 문단으로 강등(mn2). A2-c의 "8행 미만/NaN-aware"는 **골든 재현으로 관측 교차검증**되도록 재서술(0-fill이면 A3-a FAIL). `LOOKBACK=8` 상수 자체는 최소 오리엔테이션으로 잔존(관측 시퀀스 길이 설명에 필요) — 완전 제거는 미채택.

- **mn2 §A2-d "요약" 용어가 관측 밖**: `handoff.md:49`가 관측 불가 "요약" 비교로 서술(외부는 p만). A2-a와 중복 → p 기반 재서술하거나 근거 문단 강등.

> **[reviser 응답]** 해소: A2-d 성공기준을 삭제하고 **근거(train-serve skew) 문단으로 강등**, 관측 검증은 A2-a(p 차이)로 환원 명시(handoff.md §A2 근거 문단).

- **mn3 B1 run_id 하드코딩 재현성**: `handoff.md:82`. 재학습·다른 mlruns 위치 시 경로 깨짐. 동결 아티팩트라 치명적 아니나 설정 주입 권장.

> **[reviser 응답]** 해소: §B1을 "run_id 하드코딩 **금지** — 경로/run_id는 설정·env 주입(`SEPSIS_XGB_MODEL_DIR` 등)"으로 강화(handoff.md §B1).

- **mn4 A1 크로스-featureset 요청**: GRU vitals(9키)를 XGB vitals_labs(18키) 서버에 보내면 9결측→NaN으로 200(계약 통과)이나 벤치 스트림엔 퇴화 입력. 2차 하니스에서 각 모델 full featureset psv 전송을 리포트에 명시 권장.

> **[reviser 응답]** 해소: §A5(범위 밖)에 "2차 하니스는 각 모델 자기 featureset full psv 스트림, 크로스-featureset 퇴화 주의를 리포트에 명시" 추가(handoff.md §A5).

### 판정

**blocker 3건 (B1 §A3-a 골든/오라클 부재, B2 §A3-b 실패경로 트리거 불가, B3 §A4 관측불가+누수) → HOLD.** 공통 뿌리: §A 도입부 단언과 달리 A3·A4 load-bearing 성공기준이 화이트박스. 해결: (i) 관측 가능한 골든/훅을 §A에 제공, 또는 (ii) 해당 요구를 §B(main-소유 characterization)로 재배치하고 §A엔 관측 기준만. §A2-c 패딩·clip skew는 정합(PASS).

---

## 라운드 2

- 대상 commit: `f14da48` 작업트리 (reviser R1 수정 후)
- 검토일: 2026-07-01
- 핵심 질문: R1 blocker(B1·B2·B3) 실제 해소 + 새 결함/누수 여부. 판정 = 기계적 실행 / 블랙박스 검증 / 출제자(§A)-응시자(§B) 분리.
- 판정: **PASS (blocker 0). major 1 · minor 2.**

> **[지휘자 독립 검증 — 골든 재현 공백 종결]** redteam 환경엔 Bash가 없어 골든 `p`값을 실행 재현 못 했다(중대 고지). **지휘자가 `.venv/bin/python`(xgboost 3.3.0)으로 직접 재현**해 이 load-bearing 공백을 닫았다 [확인됨]:
> - vitals: A3-a 절단 `0.7098076`(핸드오프 0.70981 ✓), 비절단 `0.6923320`(0.69233 ✓), A2-a X단독 `0.6486735`(0.64867 ✓), alarm=true ✓.
> - vitals_labs: 절단 `0.8335611`(0.83356 ✓), 비절단 `0.8228457`(0.82285 ✓, gap 0.01072), alarm=true ✓.
> - 저장소 함수 그대로 사용(`config.FEATURESET_VITALS(_LABS)` 열순서, `features.lookback_summary`, `.ubj` `best_iteration`=105/149, `preprocess.json` tau). **골든 상수 5개 전부 정확 일치** → reviser 단일 [확인됨]이 아니라 독립 재현으로 확증됨.

### R1 blocker 판정

- **B1(§A3 골든 부재) → 해소됨.** §A3에 골든 시퀀스 `S_vitals`(9키 5행)가 관측 request payload로 완전 기입(`handoff.md:59-67`), 마지막 응답 기대값 `p=0.70981±1e-4, alarm=true`(`:71`), 감별력 비절단 ≈0.69233(gap 0.0175 ≫ eps). 시퀀스 명세 완전 — spec-writer가 src 없이 5회 POST 후 마지막 p 대조 RED 작성 가능. 산출 코드경로는 §B3.1에만. **지휘자 재현으로 상수값도 확증.**
- **B2(§A3-b 죽은 계약) → 해소됨.** `SEPSIS_XGB_BEST_ITER_OVERRIDE` 관측 계약(무효값→기동 실패 or 5xx, 200+전체트리 무성 폴백=FAIL, `handoff.md:82-85`), 배선 §B3.2. spec-writer가 env만으로 §B 없이 실패경로 발동 가능. `grep best_iter mlruns/`=0 확인 → env 오버라이드가 유일 실패 트리거라는 근거 사실.
- **B3(§A4 관측불가+누수) → 해소됨.** §A4가 A4-a(지표 등록)+A4-b(/predict N회→count 정확히 N 증가) 2개 관측 기준으로 재명세(`handoff.md:87-96`), "재구성 포함" 내부 타이머 경계는 §A 삭제→§B5 이관(`:96,152-156`). `metrics.record`가 `LATENCY.observe`를 호출당 1회(`metrics.py:45-46`) → A4-b GREEN 가능.

### PASS

- **골든 정적 정합** — tau 아티팩트 정확 일치(vitals `0.5467824…`, vitals_labs `0.4925572…` = preprocess.json), best_iter 산술(105→총136, 149→총180), 열순서·차원(9→63, 18→126). **+ 지휘자 실행 재현으로 부동소수값까지 확증.**
- **§B 코드 라인 참조 전수 정확** — `tree.py:69-75,74`, `predictor.py:5-7,29-34,43`, `metrics.py:18`, `app.py:96-98,77-79,103-104` 대조 정확. 거짓 [확인됨] 없음.
- **§A 화이트박스 리터럴 누수 없음(신규 없음)** — §A에 골든 상수·env 이름을 들였으나 파일/함수/라인 리터럴 없음. env 이름은 문서화된 운영 스위치(관측 인터페이스)라 위반 아님.
- **A2-a 강제 어서션 성립** — X단독 0.64867 vs 이력후 0.70981(gap 0.061 ≫ eps), 같으면 FAIL. **지휘자 재현 확인.**
- **M1(스레드 안전) 정합** — §B2가 GRU per-patient lock 패턴 정확 참조 + replicas=1·Redis 실패모드 명시.

### major

- **M-R2-1 — 골든을 지탱하는 버전 핀이 오기재·부유(floating), 결정론 논증 편도.**
  - **문제**: §B3.1(`handoff.md:139`)·§B1(`:117`)이 결정론 근거로 "requirements/.venv 핀"을 드나 **`requirements*.txt`는 부재**(Glob 확인), `pyproject.toml:19`는 `xgboost>=3.3.0`(부유 하한, `==` 아님). 실효 핀은 `uv.lock`(3.3.0)뿐인데 문서가 이를 안 가리킴 → pip/pyproject 경로 설치 시 상위 버전이 들어와 eps 1e-4 이탈 여지. 또 결정론 논증이 "eps < 감별 gap 0.0107"(상한)만 보이고, eps가 플랫폼/BLAS/패치 지터보다 큰가(하한)는 미논증.
  - **근거**: `pyproject.toml:19`(`>=3.3.0`), `uv.lock`(resolved 3.3.0), requirements 부재, `handoff.md:139`.
  - **제안**: §B에 실효 핀 명시 — "골든 RED는 `uv run`/`uv.lock`(xgboost==3.3.0) 락 환경에서만" 또는 pyproject를 `==3.3.0`으로. 그리고 spec-writer가 상수 하드코딩 전 실행자가 골든 1회 재산출·확정하도록 명시. (**지휘자가 이 재산출을 이미 수행 — 5개 상수 일치 확인.** 남은 건 핀 문서 정정.)

> **[reviser 응답]** 해소: (1) §B1(`handoff.md:117`)·§B3.1(결정론 문단)의 "requirements/.venv 핀" 문구를 **실효 핀 = `uv.lock`(xgboost==3.3.0), 실행 = `uv run` 락 환경**으로 교체하고 `requirements*.txt` 부재·`pyproject.toml:19` 부유 하한(`>=3.3.0`)을 명시 [확인됨: Glob 부재, `uv.lock:3405-3406` version 3.3.0, `.venv` 3.3.0]. pyproject `==3.3.0` 조이기 옵션도 병기. (2) 결정론 논증을 **양방향**으로 보강 — 상한(eps 1e-4 ≪ 감별 gap 0.0107)에 더해, **하한(eps > 부동소수 지터)은 `uv.lock` 락 환경 안에서만 보장**하고 락 밖(다른 패치버전/BLAS/플랫폼)에선 미보장임을 §B3.1에 경계로 명시, "골든은 락 환경 전용, 버전 오르면 재산출 필요"로 못박음. (3) §B3.1 골든 근거를 **"reviser 단독 실측" → "reviser·지휘자 2회 독립 재현으로 확증"** 으로 격상(5개 상수 명기), spec-writer의 락 환경 1회 재확인은 권장으로 유지. **추가로 발견·정정**: 기존 `handoff.md:137` X단독값 `0.6486732…`가 오기 — 실측 `0.6486734748…`로 정정 [확인됨].

### minor

- **mn-R2-1 §A/§0 개념 누수 잔존(R1 mn1 동종, 신규 아님)**: §A3-b·§0의 "best_iter 절단", A2-c "8행 미만/NaN-aware", A2 "lookback 요약"이 구현 개념 노출. 성공기준은 전부 p/count 관측으로 환원돼 블로킹 아님. 오리엔테이션 이상은 트림 권장.

> **[reviser 응답]** 해소: §0을 "환자별 raw 이력 버퍼로 시간 요약 재구성 / 챔피언과 동일한 출력 재현"으로 순화하고 버퍼 길이·요약 통계·절단 반복수는 §B로 미룬다는 문장 추가(`handoff.md:10`). §A2 서두의 "lookback 요약"을 "과거 행+이번 행을 함께 써서(시간 이력 반영)"로 대체(`:39,41`). A2-c의 "8행 미만/NaN-aware"를 "이력이 짧을 때 / 학습과 동일한 결측 처리"로 재서술(`:51`) — 성공기준(0-fill이면 A3-a FAIL)은 그대로. **미채택 잔존**: §A3-b의 "절단 반복수" 서술은 환경변수 `SEPSIS_XGB_BEST_ITER_OVERRIDE` 이름 자체가 best_iter를 노출하므로(문서화된 운영 스위치 = 허용된 관측 인터페이스), 프로세만 지워도 실익 없고 오히려 스위치 의미가 흐려짐 → 유지. A3-a 감별력 문단의 "절단" 개념은 골든 두 상수의 차이를 설명하는 load-bearing이라 유지(성공기준 자체는 건드리지 말라는 지시 준수).

- **mn-R2-2 A3-a 감별력이 "절단 유무" 단일 축**: 골든 대조는 characterization이라 절단 외 오류가 우연히 0.70981±1e-4 착지하면 거짓 통과 가능(본질적 한계). 필요시 중간 행 골든 1개 추가로 궤적 고정(선택).

> **[reviser 응답]** 해소(채택): §A3에 **중간 행 골든 A3-a-mid** 추가 — `S_vitals`의 **3번째 응답** `p = 0.24634 ± 1e-4`, `alarm=false`(`handoff.md` §A3-a). **실제 산출값** [확인됨: `.venv` xgboost 3.3.0, 앞 3행 버퍼→`lookback_summary`→best_iter=105 절단 = `0.2463367581…`]. §B3.1에 재현 경로도 병기. 이제 궤적이 마지막 값 하나가 아니라 중간+끝 두 지점에 고정돼 절단 외 오류가 우연히 끝값만 착지하는 거짓 통과를 걸러낸다.

### 판정

**라운드 2 blocker 0건 → PASS.** R1 blocker 3건 모두 해소, 새 누수 없음, §B 코드참조 전수 정확. **골든 load-bearing 공백은 지휘자 독립 재현으로 종결(5개 상수 일치).** 남은 major M-R2-1(버전 핀 오기재·부유)은 blocker 아니나 CI 재현성 위해 정정 권장. minor 2건.
