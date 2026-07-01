# Serving-Benchmark 구현 핸드오프 (명세부) — 1차: XGB 최소 서빙

> **전제**: `docs/design/serving-benchmark/decisions.md`(설계부) v5, 5라운드 검토 통과(blocker 0). 본 문서는 그 **결정 2 + PASS 게이트 1**(XGB 최소 서빙)만 자립형으로 명세한다. 계측 하니스(결정 3·4)·비용표(결정 5)·관측성 게이트(NB3)는 **2차 핸드오프**로 분리한다.
> **워크플로우**: 검토(`handoff_review.md`) 통과 → **spec-writer가 §A만 보고 TDD(RED)** → **main이 §A+§B로 구현(GREEN)**. 푸시는 사람 게이트(자동 금지).
> **출제자-응시자 분리**: **§A(계약·성공기준·실패모드)는 spec-writer 전용** — src 라인 참조 없이 관측 가능한 행동으로만 기술. **§B(구현 참조)는 main 전용** — spec-writer는 §B를 읽지 않는다.
> **상태**: 명세부 v1 — 레드팀 검토 전.

## 0. 한 줄 요약

GRU 서빙과 **같은 `/predict` 계약**을 따르는 **XGBoost 최소 서빙 앱**을 세운다. 핵심 난점 셋 — (1) XGB는 stateless가 아니라 **환자별 8행 raw 버퍼**로 lookback 요약을 서버측에서 재구성해야 하고, (2) 챔피언 재현을 위해 `.ubj` 임베드 **best_iter로 트리를 절단**해야 하며, (3) latency 지표가 GRU와 **같은 범위를 재도록** 대칭을 맞춰야 한다(경계 세부는 §B5). 이 1차 핸드오프는 **XGB 서빙만** 세운다(벤치 측정·비교는 2차).

---

# §A. spec-writer 전용 — 계약·성공기준·실패모드

> spec-writer는 이 절만 읽고 TDD 테스트를 작성한다. 아래는 전부 **관측 가능한 입출력 행동**으로만 검증한다 — HTTP 요청/응답, `/metrics` 카운터, 그리고 **출제자가 동결 아티팩트로 미리 산출해 박아둔 골든 상수**(A3)와 **문서화된 운영 오버라이드 스위치**(A3-b)를 통한 관측이다. 내부 구현(어느 파일·어느 함수·타이머 경계)은 알 필요 없고, 알아서도 안 된다(출제자-응시자 분리).

## A1. `/predict` 계약 (GRU와 동일 — 대칭 필수)

XGB 최소 서빙은 GRU 서빙과 **동일한 요청/응답 스키마**를 노출한다.

**요청** (POST `/predict`):
```json
{"patient_id": "p000001", "features": {"HR": 88.0, "O2Sat": 97.0, "...": null}}
```
- `features`는 **단일 timestep 한 행**의 raw 값. 클라이언트는 8행을 보내지 **않는다**(서버가 버퍼로 재구성).
- 값이 없으면 **`null` 또는 키 부재** → 내부적으로 결측(NaN)으로 취급. **0으로 채우지 않는다.**
- featureset이 `vitals`면 9개 피처, `vitals_labs`면 18개 피처 키.

**응답** (성공 시):
```json
{"patient_id": "p000001", "p": 0.42, "alarm": false, "featureset": "vitals_labs"}
```
- **정확히 네 키**: `patient_id`, `p`(0~1 확률), `alarm`(bool), `featureset`(str). 더도 덜도 아님.
- `alarm` = `p >= tau`. `tau`는 모델 번들에 동결된 값(요청마다 재계산·재선택 금지).

**성공기준 A1**: GRU 서빙에 보내던 리플레이어 요청을 **수정 없이** XGB 서빙에 보내도 200 응답 + 네 키가 온다. 422/500이 안 난다.

## A2. 환자별 상태 = lookback 버퍼 (stateless 아님)

XGB 서빙은 **환자별로 최근 raw 행들을 기억**한다. 매 `/predict`가 오면 그 환자의 과거 행 + 이번 행으로 **lookback 요약**을 만들어 추론한다.

**관측 가능한 행동으로 정의한 성공기준:**

- **A2-a (상태 존재 = 같은 입력도 이력에 따라 다른 출력 — 반드시 다르다)**: §A3의 골든 시퀀스 `S_vitals`의 **마지막 행 X**(`{"HR":112,"O2Sat":93,"Temp":38.5,"SBP":98,"MAP":70,"DBP":58,"Resp":24,"Age":64,"Gender":1}`)를,
  - (i) 새 `patient_id`의 **첫 요청**으로 단독 전송하면 `p ≈ 0.64867 ± 1e-4`,
  - (ii) `S_vitals` 5행을 순서대로 보낸 뒤(마지막 요청이 곧 X)의 응답은 `p ≈ 0.70981 ± 1e-4`.

  두 값은 **반드시 다르다**(약 0.061 차 ≫ eps). 같으면 서버가 이력을 기억하지 않는 것 = **FAIL**. (두 기대값 모두 §A3 골든과 같은 동결 아티팩트에서 산출한 관측 상수.)
- **A2-b (환자 격리)**: 환자 P의 행들과 환자 Q의 행들을 **번갈아** 보내도, P의 예측은 P의 이력으로만·Q는 Q의 이력으로만 계산된다. 한 환자의 버퍼가 다른 환자에 새지 않는다. (구체 검증: P에게 `S_vitals`를, 그 사이사이 Q에게 다른 행들을 끼워 보내도, P의 5번째 응답이 A2-a (ii)의 `0.70981 ± 1e-4`와 같다 — 교차 요청이 P의 버퍼를 오염시키지 않음.)
- **A2-c (버퍼 부족분 = 학습과 동일한 처리)**: 첫 몇 요청(과거가 8행 미만)에도 500 없이 유효한 `p`를 반환한다. 부족분을 **0으로 채우지 않는다**. 이 "0-fill 아님"은 별도 화이트박스 검사 없이 **A3-a 골든 재현으로 관측 교차검증된다**: 골든의 1~4번째 요청은 과거가 8행 미만이며, 골든 기대값은 부족분을 NaN-aware로 처리해 산출됐다. 서버가 부족분을 0으로 채우면 마지막 `p`가 골든과 불일치 → A3-a가 FAIL로 잡는다.

> **근거 (왜 상태가 필수인가 — train-serve skew)**: 서버가 과거를 안 기억하고 **매 요청 1행만으로** 특징을 만들면 시간적 변화 정보(변화량·분산·최소/최대 등)가 퇴화해 학습 때 본 입력 분포와 어긋난다(train-serve skew). 이 위험을 **관측으로 잡는 성공기준은 A2-a**(같은 행 X가 이력 유무에 따라 0.649 vs 0.710으로 갈림)가 대표한다. 서버 외부에서는 내부 "요약값"을 직접 볼 수 없으므로, 요약 비교 자체를 성공기준으로 두지 않고 p 차이로 환원한다.

## A3. 챔피언 재현 골든 (load-bearing)

같은 입력 시퀀스에는 항상 같은 확률이 나와야 하고, 그 값은 **동결 챔피언 모델의 값**이다. 아래 골든은 **출제자가 동결 아티팩트로 미리 산출한 관측 기대값**이다 — spec-writer는 서버 응답을 이 상수와 대조만 하면 되고, 참조 모델을 스스로 로드·재구성할 필요가 없다(순환 테스트 회피).

**골든 시퀀스 `S_vitals`** (featureset=`vitals`, 9키, 5행 — 새 `patient_id`로 아래 순서대로 5회 `/predict` 전송):

```
1  {"HR":80,  "O2Sat":98, "Temp":37.0, "SBP":120, "MAP":85, "DBP":70, "Resp":16, "Age":64, "Gender":1}
2  {"HR":88,  "O2Sat":97, "Temp":37.2, "SBP":118, "MAP":83, "DBP":68, "Resp":18, "Age":64, "Gender":1}
3  {"HR":95,  "O2Sat":96, "Temp":37.6, "SBP":110, "MAP":78, "DBP":64, "Resp":20, "Age":64, "Gender":1}
4  {"HR":104, "O2Sat":94, "Temp":38.1, "SBP":102, "MAP":72, "DBP":60, "Resp":22, "Age":64, "Gender":1}
5  {"HR":112, "O2Sat":93, "Temp":38.5, "SBP":98,  "MAP":70, "DBP":58, "Resp":24, "Age":64, "Gender":1}
```

**성공기준:**

- **A3-a (골든 재현)**: 위 5행을 순서대로 보낸 뒤 **5번째(마지막) 응답**의 `p` = **`0.70981 ± 1e-4`**, `alarm` = **true**(동결 `tau` 기준). 이 값은 동결 챔피언의 출력이다.
  - **관측 감별력**: best_iter 절단을 적용하지 **않은**(전체 트리) 서버는 같은 시퀀스에 `p ≈ 0.69233`을 반환한다 — 골든과 약 **0.0175** 차(≫ eps 1e-4). 따라서 "마지막 p가 골든과 일치"는 *절단이 실제로 걸렸음*을 외부에서 관측 증명한다. (예전 A3-a의 "전체 트리 예측과 다르다"는 서버가 그 값을 반환하지 않아 검증 불가였음 → 골든 상수로 대체.)
  - **(선택) `vitals_labs` 18키 서버**를 함께 세우면, 아래 골든 시퀀스 `S_labs`(featureset=`vitals_labs`, 5행 — 명시된 키만 값, **나머지 lab 키는 부재=NaN**)의 마지막 `p` = **`0.83356 ± 1e-4`**, 비절단 시 ≈`0.82285`(gap 0.0107 ≫ eps):

    ```
    1  {"HR":80, "O2Sat":98, "Temp":37.0, "SBP":120, "MAP":85, "DBP":70, "Resp":16, "Age":64, "Gender":1, "WBC":8.0, "Lactate":1.2, "Creatinine":0.9}
    2  {"HR":88, "O2Sat":97, "Temp":37.2, "SBP":118, "MAP":83, "DBP":68, "Resp":18, "Age":64, "Gender":1}
    3  {"HR":95, "O2Sat":96, "Temp":37.6, "SBP":110, "MAP":78, "DBP":64, "Resp":20, "Age":64, "Gender":1, "Lactate":2.1}
    4  {"HR":104,"O2Sat":94, "Temp":38.1, "SBP":102, "MAP":72, "DBP":60, "Resp":22, "Age":64, "Gender":1, "WBC":13.5}
    5  {"HR":112,"O2Sat":93, "Temp":38.5, "SBP":98,  "MAP":70, "DBP":58, "Resp":24, "Age":64, "Gender":1, "Lactate":3.4, "Creatinine":1.4}
    ```
- **A3-b (무효 best_iter → 관측 가능한 실패, 조용한 폴백 금지)**: 서버에는 절단 반복수를 강제로 덮어쓰는 **문서화된 운영 오버라이드** 환경변수 `SEPSIS_XGB_BEST_ITER_OVERRIDE`가 있다.
  - 이 값을 **무효값**(`0`·음수·`none`)으로 설정하고 서버를 기동하면, 서버는 **관측 가능하게 실패**한다 — 기동 실패(프로세스 비정상 종료 또는 헬스체크 실패) **또는** `/predict`가 5xx/명시적 에러 응답. **200 + 전체-트리 확률로 조용히 폴백하면 FAIL.**
  - 이 오버라이드를 **설정하지 않으면**(정상 운영) 서버는 아티팩트 임베드값을 써 A3-a 골든을 정상 재현한다.
  - 이 스위치가 A3-b의 실패 경로를 **발동 가능**하게 만든다(임베드 best_iter가 항상 양수라 실제 아티팩트만으로는 실패를 트리거할 수 없기 때문).

## A4. latency 계측 존재·발동 (GRU와 대칭)

XGB 서빙도 GRU와 **같은 이름의 latency 지표** `serve_predict_latency_seconds`(Prometheus Histogram)를 `/metrics`에 노출한다.

**성공기준 A4 (전부 관측 가능):**

- **A4-a (지표 등록)**: `/metrics` 응답에 `serve_predict_latency_seconds_count`와 히스토그램 버킷(`_bucket`)·합계(`_sum`)가 존재한다.
- **A4-b (호출당 정확히 1회 계측)**: `/predict`를 성공적으로 **N회** 호출하면 `serve_predict_latency_seconds_count`가 호출 전 대비 **정확히 N 증가**한다(호출 1회 = 계측 1회).

> latency 지표가 감싸는 **내부 구간의 경계**(요약 재구성 포함 여부)는 GRU와의 비교 대칭을 위한 **구현 요구**이며, 외부에서 관측 불가하므로 이 절의 성공기준이 아니다. 그 경계 요구는 구현자 몫으로 분리한다(§A 밖). spec-writer는 지표의 존재·발동만 검증한다.

## A5. 범위 밖 (이 핸드오프에서 만들지 않음)

- 계측 하니스(client 벽시계 래핑, throughput 부하, 메모리 RSS 측정) — 2차
- arm-1/arm-2 계측 대칭, 통제 arm, 관측성 env 게이트 — 2차
- 비용 환산표 — 2차
- GRU 서빙 코드 변경 — 하지 않음(XGB는 독립 앱)
- 트랜스포머 — 범위 밖
- 2차 벤치 하니스에서 각 모델은 **자기 featureset의 full psv**를 스트림해야 한다(GRU `vitals` 9키를 XGB `vitals_labs` 18키 서버에 보내면 9키 결측→NaN으로 200은 나오나 퇴화 입력). 이 크로스-featureset 주의는 2차 하니스 리포트에 명시 — 이 1차 범위 밖.

---

# §B. main 전용 — 구현 참조

> **spec-writer는 이 절을 읽지 않는다.** 아래는 §A의 성공기준을 통과시키기 위한 구현 앵커다. 경로·라인은 설계부 v5에서 `[확인됨]`으로 검증된 것.

## B1. 아티팩트 로드

- 부스터: `mlruns/1/3e21f380b380422d8d52f78904e54ad4/artifacts/model/xgboost_vitals.ubj`(9피처) · `mlruns/1/fe64aac54f344999baa217f56e4e963c/artifacts/model/xgboost_vitals_labs.ubj`(18피처). **run_id 하드코딩 금지 — 경로(또는 run_id)는 설정/env 주입**(예: `SEPSIS_XGB_MODEL_DIR` 또는 featureset→경로 매핑 config). 재학습·다른 `mlruns` 위치에서도 깨지지 않게. [mn3]
- `tau`: 각 run의 `artifacts/preprocess.json` — keys `['featureset','scale_pos_weight','tau','hp','note']`. **정규화 통계(mean/std) 없음이 정상**(XGB는 트리 NaN-native).
- **best_iter**: `.ubj`에 임베드됨. `xgb.Booster().load_model(<.ubj>)` 후 `b.best_iteration` → **105**(vitals) / **149**(vitals_labs)로 실측 확인(xgboost 3.3.0). run 스토어 조회 불필요(이 저장소 mlruns는 artifacts-only pruned, `grep best_iter mlruns/`=0).

## B2. lookback 버퍼 → 요약 재구성

- 요약 함수: `src/sepsis/data/features.py`의 `lookback_summary`가 `(T,F)→(T,F*7)` 변환. row t = 윈도우 `[t-7..t]`의 7종 통계(`config.py:61-62` `LOOKBACK=8`, `TREE_STATS` 7종). vitals 9→63, vitals_labs 18→126차원.
- 앞 패딩은 학습과 동일하게 NaN-aware(`features.py:25` `_windows`가 앞을 NaN 패드).
- 버퍼: 환자별 최근 8행 raw를 유지(자료구조·소멸정책·직렬화는 main 재량). 요청 raw 1행 → 버퍼에 push → 버퍼로 `lookback_summary` 호출 → 마지막 행 요약이 이번 입력.
- **스레드 안전성·실패모드(M1)**: throughput 부하(2차 `replay_many` ThreadPoolExecutor)에서 같은 환자의 동시 요청이 버퍼 read-modify-write를 겹치면 A2-b 격리가 깨질 수 있다. **환자별 lock으로 동일 환자 요청을 직렬화**하라 — GRU `StatefulPredictor`의 per-patient lock 패턴(`src/sepsis/serve/predictor.py:5-7,29-34,43` — `_lock(pid)` 레지스트리 + `with self._lock(pid)`로 상태 갱신 감싸기)과 동일하게. **`replicas=1` 가정**(인메모리 버퍼); `replicas>1`이면 공유 스토어(예: Redis)가 필요 — 이 1차 범위 밖이나 실패모드로 명시.

## B3. best_iter 절단 추론

- `src/sepsis/train/tree.py:69-75` `booster_predict`가 xgboost일 때 `iteration_range=(0, int(best_iter)+1)`로 절단. 이 함수 재사용 또는 동일 로직.
- 유효성: `tree.py:74` `rng = (0, int(best_iter)+1) if best_iter and best_iter >= 0 else None` — falsy/음수면 None(전체트리). **§A3-b대로 이 무성 폴백을 XGB 서빙에선 금지**하고 명시적 실패로 처리(예: 기동 시 best_iter 유효성 assert).
- 채점 경로 참고: `eval/crosssite.py:60,65`가 `score_tree_frozen(booster, model_name, best_iter, tau, …)`로 절단 주입하는 패턴과 동일.

### B3.1 §A3 골든 산출 근거 (출제자 제공 — main은 재현만 확인)

§A3의 골든 상수는 **동결 `.ubj` + `lookback_summary` + best_iter 절단**으로 산출됨 [확인됨: xgboost 3.3.0, `.venv/bin/python`으로 실측]. 재현 경로:
- 입력: §A3의 `S_vitals` 5행을 `(5,9) float32` 배열로(열 순서 = `config.FEATURESET_VITALS` = HR,O2Sat,Temp,SBP,MAP,DBP,Resp,Age,Gender). `features.lookback_summary(raw)` → `(5,63)`, **마지막 행**이 5번째 요청의 추론 입력.
- 절단: `tree.load_booster("xgboost", <vitals.ubj>)` → `b.best_iteration=105`(총 트리 136), `tree.booster_predict(b,"xgboost",last,105)` → **`p=0.7098076…`** (반올림 `0.70981`). 비절단(`b.predict(DMatrix(last))`, 전체 트리) → `0.6923320…`. `tau=0.5467824…`(`preprocess.json`) → `alarm=true`.
- A2-a용: `S_vitals` **마지막 행 X 단독**을 1행 버퍼로 요약→절단 예측 → `p=0.6486732…`(반올림 `0.64867`).
- `vitals_labs`(18키) 골든 `S_labs`: `best_iteration=149`(총 180), `p_절단=0.8335611…`, `p_비절단=0.8228456…`, `tau=0.4925572…`. `S_labs`는 labs 대부분 결측(null)인 5행 시퀀스 — main이 자기 검증을 원하면 §A3 감별력과 동일 방식으로 구성(값 세트는 이 저장소 `.venv`로 재산출 가능; 1차 필수는 `S_vitals` 골든뿐).
- **결정론**: 같은 xgboost 버전(3.3.0, `requirements`/`.venv` 핀)·같은 `.ubj`에서 float 재현. eps `1e-4`는 절단↔비절단 간 최소 gap `~0.0107`(vitals_labs)보다 두 자릿수 작아 감별 안전.

### B3.2 §A3-b best_iter 오버라이드 훅 (실패경로 트리거 수단)

- §A3-b가 요구하는 실패 경로를 발동 가능하게 하려면, best_iter 결정 지점에 **오버라이드 주입구**를 둔다: 환경변수 `SEPSIS_XGB_BEST_ITER_OVERRIDE`가 설정돼 있으면 임베드 `b.best_iteration` 대신 그 값을 사용.
- **유효성 게이트(무성 폴백 금지)**: 최종 best_iter가 유효 양수(≥1)가 아니면(오버라이드가 `0`·음수·`none`/비정수, 또는 임베드값 복구 실패) `tree.py:74`의 `else None`(전체트리) 경로로 **넘어가지 말고** 기동 시 명시적으로 실패(예: `raise`/assert로 프로세스 종료 또는 헬스체크 FAIL). 이래야 §A3-b가 "기동 실패 또는 5xx"로 관측된다.
- 오버라이드 미설정 = 정상: 임베드 105/149를 써 §A3-a 골든을 재현. (테스트 훅 대안으로 무효 `best_iteration` 크래프트 `.ubj` 픽스처도 가능하나, env 오버라이드가 부작용 없고 관측이 명확해 권장.)

## B4. `/predict` 계약 참조 (GRU)

- GRU 요청/응답: `app.py:77-79,103-104`. 응답 네 키 `{patient_id, p, alarm, featureset}`.
- 결측 계약: absent/null → NaN, 0-fill 금지.

## B5. latency 히스토그램

- GRU 참조: `metrics.py:18` `LATENCY = Histogram("serve_predict_latency_seconds", ...)`, `app.py:96-98`이 `t0=perf_counter(); out=pred.predict(...); metrics.record(perf_counter()-t0, ...)`. GRU `predict()`는 전처리(`predictor.py:44` `StreamPreprocessor.step`)를 포함하므로 히스토그램이 전처리+forward를 잼.
- XGB 대칭: 관측 구간이 **버퍼 재구성(`lookback_summary`) + `booster_predict`(절단)** 를 함께 감싸야 함. `booster.predict`만 감싸면 GRU와 범위 비대칭.
- **경계 소유권**: 이 "재구성 포함" 경계 요구는 **§B(main) 전용**이다 — 외부에서 관측 불가하므로 §A4의 성공기준으로 두지 않는다(§A4는 지표 존재·호출당 count 증가만 관측). 경계 대칭은 main이 코드로 보장하고, 리뷰/PR에서 육안 확인.

## B6. 앱 구성 결정 (main 재량)

- 새 앱(`serve/xgb_app.py` 류) vs 기존 serve 모듈 분기 플래그 — main 판단. **단 GRU 예측/추론 로직을 오염시키면 안 됨**(PASS 게이트 1). 새 앱이 오염 위험이 낮음.

---

## 핸드오프 검토 요청 항목 (redteam이 팔 자리)

1. §A2의 "관측 가능한 행동" 성공기준이 실제로 버퍼 상태를 **블랙박스로** 검증 가능한가(spec-writer가 src 안 보고 테스트 가능한가). — R1 후: A2-a/b가 §A3 골든 상수(0.649/0.710)에 묶여 관측화됨.
2. §A4가 **관측 가능 기준**(지표 존재 + 호출당 count 정확히 1 증가)으로만 재명세됐는가 — 내부 타이머 경계는 §B5 소유로 이관(R1 B3 해소). 남은 누수 없나.
3. §A3-b 실패경로가 `SEPSIS_XGB_BEST_ITER_OVERRIDE`로 **발동 가능**한가 — spec-writer가 env 무효값 설정→기동/응답 실패를 관측으로 검증 가능한가(R1 B2 해소).
4. §A3-a 골든 상수(`S_vitals`→0.70981±1e-4)가 **출제자 제공·순환 아님·결정론적**인가 — 다른 xgboost 버전/환경에서 eps 1e-4가 깨질 위험(§B3.1 결정론 근거 확인).
4. B1 run_id 하드코딩이 재현성을 해치는지(다른 환경에서 mlruns 경로가 다를 때).
5. §A2-c 버퍼 부족분 NaN-aware 처리가 학습과 정말 동일한가(패딩 방식 skew).