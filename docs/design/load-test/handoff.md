# 부하테스트 (나) — 구현 핸드오프 (경량)

> **입력**: `docs/design/load-test/decisions.md` v3 (레드팀 3R 통과, blocker 0). **이 핸드오프가 설계→구현 번역의 권위**이며, 충돌 시 실물 코드가 최종 판정.
> **성격**: (나)는 **프로덕션 코드를 안 건드리는 측정 드라이버**다. Locust 시나리오가 곧 구현이라 두꺼운 번역 문서 불필요 — 핵심만. src/ 시그니처는 대조 기입.
> **출처등급**: `[확인됨]`(코드 대조) · `[핸드오프 결정]` · `[검증 필요]`(런타임 실측=SM).
> **상태**: 핸드오프 v1.

---

## 0. 스코프 한 줄

Locust로 "가상 User = 환자 1명"을 만들어 각 환자 setB PSV를 timestep 순서로 `/predict`에 흘려보내고, 동시 환자 수 N × 코어를 sweep해 saturation knee와 여유를 실측한다. **프로덕션(가) 스택은 밖에서 부하만 때린다 — 코드 0줄 변경.** 실제 부하 실행(SM)은 사람이 스택 `up` 후 도는 것이라 이 핸드오프는 **드라이버 코드까지**.

---

## 1. 파일 배치

| 파일 | 역할 | TDD 대상? |
|---|---|---|
| `loadtest/patient_pool.py` | 미사용 환자 배타 배정(스레드세이프), 반복 금지, 고갈 처리 | ✅ RED (핵심 로직) |
| `loadtest/request_builder.py` | PSV 행 dict → `/predict` 페이로드 조립 | ✅ RED |
| `loadtest/locustfile.py` | `HttpUser` = 환자, on_start 배정·PSV 로드, task 순서 전송 | ⛔ (Locust 런타임 — 스모크) |
| `loadtest/run_matrix.py` | N×코어 매트릭스 실행·서버 재시작·워밍업컷·측정 수집 | ⛔ (오케스트레이션 스크립트) |
| `tests/bench/test_load_driver.py` | 위 ✅ 로직의 RED | — |

> `loadtest/` 신규 디렉토리. 기존 `sepsis.bench`(서빙 벤치 하니스, src/)와 별개. Locust는 dev 의존성(`uv add --dev locust`) [핸드오프 결정].

---

## 2. 컴포넌트 (실물 대조 기입)

### 2.1 `PatientPool` (① 로직 — B1·M1·B1-r2 핵심)

- **역할**: setB PSV 파일 풀에서 **아직 아무 User도 안 쓴 환자를 배타적으로 하나씩** 내준다. **반복(재사용) 금지** — 같은 pid 재전송 시 서버 hidden state 오염(결정 2 B1, `psv_source.py:10-12` F4). 고갈 시 `None` 반환(더 줄 환자 없음 → User 정지, 결정 2 B1-r2 "풀 고갈 전 유한 지속").
- **소스**: `data/raw/training_setB/*.psv` (20,000개) [확인됨: `config.py:33 SITE_COUNTS training_setB:20000`; glob 20,000 실측]. 경로는 `sepsis.config`의 `DATA_DIR/"training_setB"` [확인됨: `config.py:13 DATA_DIR = ROOT/"data"/"raw"`].
- **계약**:
  - `claim() -> Path | None`: 미사용 파일 하나를 배타 반환(반환 즉시 used 마킹). 풀 소진 시 `None`. **스레드세이프**(Locust User는 여러 그린렛/스레드 — 두 User가 같은 파일을 못 받게 락 또는 원자 pop).
  - 한 번 claim된 파일은 **다시 claim 안 됨**(반복 금지·배타 배정 동시 충족).
  - `total`·`remaining` 조회 가능(고갈 임박 판단·러너 로깅용).
- **주의**: 셔플은 해도 되나(대표성) 배타성·비반복이 불변. run 간 재현 위해 seed 옵션 [핸드오프 결정].

### 2.2 `request_builder` (① 로직)

- **역할**: PSV 행 dict + patient_id → `POST /predict` 페이로드.
- **계약** [확인됨: `app.py:77-79 PredictRequest`]:
  - 페이로드 = `{"patient_id": str, "features": {col: float|None}}`.
  - **결측 보존**: PSV NaN → `None` 그대로(0/평균 채움 금지) — `PsvRowSource`가 이미 `{col: None|float}` 산출(`psv_source.py:46-48`)하므로 **그 dict를 그대로 features로** 넣으면 됨.
  - **features 키는 featureset 컬럼의 부분집합만** — 초과 키가 있으면 서버가 **422**(`app.py:81-84 _row_from`: `unknown = set(features)-set(cols)` → HTTPException 422). `PsvRowSource(featureset="vitals")`는 `C.featureset_columns("vitals")` = `FEATURESET_VITALS`(9컬럼, `config.py:51`)만 담으므로 자연 충족 [확인됨].
- **응답**(참고, 검증 불필요): `{patient_id, p, alarm, featureset}` [확인됨: `app.py:106-107`].

### 2.3 `locustfile.py` (Locust `HttpUser` — 스모크 대상)

- `class SepsisPatientUser(HttpUser)`:
  - **`on_start`**: `PatientPool.claim()`으로 환자 하나 배타 확보 → `PsvRowSource(path, featureset="vitals")`로 행 스트림 로드(`replay/psv_source.PsvRowSource` — DDD 결정 3 지목 로더). claim이 `None`이면 `self.environment.runner.quit()` 또는 User stop(고갈).
  - **`@task`**: 다음 timestep 행을 꺼내 `request_builder`로 페이로드 조립 → `self.client.post("/predict", json=payload)`. 스트림 끝나면 **반복하지 않고** `PatientPool.claim()`으로 **다음 미사용 환자로 교체**(결정 2). 교체분도 없으면 정지.
  - **순서 보장**: 한 User가 한 환자 행을 **파일 순서대로** 전송(`PsvRowSource.__iter__`가 파일=시간순, `psv_source.py:44,50`). 쪼개거나 재배치 금지(causal).
- **타깃**: `--host http://localhost:8000`(serving 직접 포트, 가 SM 확인). front-nginx 우회(부하 측정에 프록시 홉 배제, 가 결정 3).

### 2.4 `run_matrix.py` (매트릭스 오케스트레이션 — 스크립트)

각 칸(cell)마다:
1. **서버 재시작 + 프리웜** (M-r2-1·m-r3-1): 칸마다 serving 컨테이너 재시작(`docker compose ... restart serving` 또는 down/up)으로 서버 pid 상태(`_h`·`_locks`·`_last`) 리셋 → 칸 간 causal 오염·누적 이월 차단. **재시작 직후 프리웜 요청 1건**으로 lazy-boot(300-trial 캘리브레이션, `app.py:56-73`)를 부하 구간 밖에서 태운다. (재시작 없는 연속 칸이라도 최소 프리웜은 매 칸.)
2. **코어·스레드캡 동반 설정** (결정 5): 코어축 칸에서 `cpus`와 `*_NUM_THREADS`(OMP/OPENBLAS/MKL/NUMEXPR)를 **동수로 함께** 변경(두 변수 혼입 방지). N축 칸은 코어 2 고정.
3. **측정 오염 방지** (결정 4): per-patient gauge **off**(`SERVE_PER_PATIENT_GAUGE` 미설정/0), prometheus scrape **15s**(2s는 데모용).
4. **부하 실행**: Locust headless(`--headless -u N -r <ramp> --host ... --run-time <T>`). 램프업 완료 후 **첫 30초 컷** + `--reset-stats`로 워밍업 표본 제거(M2). 정상상태 창만 집계.
5. **수집** (결정 6): Locust RPS·p50/p95/p99·에러율(csv/history) + `docker stats`/prometheus 메모리·CPU. 칸별로 저장.
6. **CPU 경고 오라클** (결정 4.3): Locust 로그의 CPU 경고를 파싱/기록 — 무릎 전에 경고 뜨면 그 칸은 "생성기의 무릎(무효)"로 표시.
- **N축**: `1·10·50·200·500·1000`(무릎까지 연장). **코어축**: `1·2·4`(N=200). 십자 ~8칸(결정 5).

### 2.5 SM-3 (부하 중 reload) — 러너의 특수 칸

- N=200 지속 부하 중 `POST /admin/reload` 트리거([확인됨: `app.py:141 /admin/reload`]) → 메모리 관측.
- **판정 = OOM/메모리, latency 아님** (결정 7 m3): OOM-kill 무발생·mem_limit(2g) 내 수용이 합격. reload 창의 latency 팽창은 **결정 5 용량 매트릭스 p99에 합산하지 않고 별도 창으로** 기록(핫패스는 `_LOCK` 미획득이라 락 스톨 없음).
- **메모리 해석** (결정 7 B1-r2): 관측 메모리 = 서버 pid 누적분(풀 상한 수십 MB, 완만 상승) + **reload 순간 증분**(bundle 이중화 창, torch 런타임은 1회 로드라 복제 안 됨 → "RSS 2배" 아님). OOM 판정은 누적분 baseline 제외 후 **순간 증분**으로.

---

## 3. TDD RED 대상 (spec-writer — 시나리오 로직만)

`tests/bench/test_load_driver.py`. **RED**: 대상 모듈 없음/계약 위반 → 실패. 매트릭스 실행·리포트는 스크립트라 TDD 대상 아님.

- **T1 (배타 배정)**: `PatientPool`에서 연속 `claim()` N회 → **서로 다른 파일** N개(중복 0). (M1 배타 distinct.)
- **T2 (반복 금지)**: 이미 claim된 파일이 다시 claim되지 않음 — 풀 크기 K면 claim은 최대 K회 성공 후 `None`. (B1 비반복.)
- **T3 (고갈)**: 풀 소진 후 `claim()` → `None`(예외 아님). (B1-r2 유한.)
- **T4 (스레드세이프)**: 여러 스레드에서 동시에 claim해도 같은 파일이 두 번 안 나옴(중복 0). (배타 배정 동시성.)
- **T5 (요청 스키마 조립)**: PSV 행 dict(예: `{"HR":88.0,"O2Sat":None,...}`)+patient_id → `{"patient_id": str, "features": {...}}`, **None 보존**(0/평균 아님), features 키 ⊆ featureset 컬럼. (결측 계약·스키마.)
- **T6 (PSV 순서 보존)**: `PsvRowSource`(또는 러너의 순서 로직)가 파일 행 순서를 재배치 없이 그대로 산출. (causal 순서.)

> 실 setB PSV에 의존하면 테스트가 무거우니, **tmp에 소형 합성 .psv**(파이프 구분·헤더·NaN 셀 포함)를 fixture로 만들어 검증 권장 [핸드오프 결정]. `PatientPool` 소스 디렉토리를 주입 가능하게(기본 setB, 테스트는 tmp) 설계.

---

## 4. 측정 오염 방지 체크리스트 (결정 4 — 구현 강제)

- [ ] BLAS `*_NUM_THREADS` = `cpus` 동수(코어축 칸마다 동반 변경).
- [ ] per-patient gauge **off**, prometheus scrape **15s**.
- [ ] 프리웜 1건(칸마다) + 램프업 후 첫 30초 컷 + `--reset-stats`.
- [ ] Locust CPU 경고 파싱 — 무릎 전 경고 시 그 칸 무효 표시.
- [ ] 칸마다 서버 재시작(서버 pid 리셋, 칸 간 이월 차단).
- [ ] 타깃은 serving 직접 `:8000`(front-nginx 우회).

---

## 5. 범위 밖 (구현하지 말 것)

- 프로덕션 코드(app.py·predictor.py 등) 변경 — 부하는 밖에서만. `/reset` 엔드포인트 추가 금지(범위 밖 → 미사용 환자 교체로 회피).
- 실제 부하 실행·리포트 수치 — 사람이 스택 `up` 후 러너 실행(SM). 이 핸드오프는 드라이버 코드까지.
- `run_suffix` 무한 pid 재활용 — 서버 pid 무한 증가로 결정 7 OOM 되살아남(결정 2 B1-r2, 불채택).
- XGB·멀티노드·replica≥2 — 범위 밖.

## 6. 부하 실행 검증 항목 (SM / [검증 필요] — 사람이 up 후)

- **SM-3**: N=200 지속 중 `/admin/reload` → mem_limit 2g 내 OOM 없음(순간 증분 판정).
- **CPU 경고 무발생**: 무릎 지점/이하에서 Locust CPU 경고 침묵.
- **무릎 위치**: 실측 무릎이 병원 부하(초당 <1)의 몇 배.
- **서버 메모리 기울기**: 지속 부하에서 서버 누적(풀 상한 수십 MB)이 유계인지 — 완만 단조 상승 정상, 급격/무한이면 시나리오 버그(결정 6).

---

## 7. 시그니처 대조 요약 (실물)

- `PredictRequest{patient_id: str, features: dict[str,float|None]}` [확인됨: `app.py:77-79`]. 초과 키 → 422 [확인됨: `app.py:81-84`].
- `PsvRowSource(path, featureset="vitals", patient_id=None, run_suffix=None)` → `{col:None|float}` 파일순 [확인됨: `psv_source.py:26-51`].
- `C.featureset_columns("vitals")` = `FEATURESET_VITALS`(9컬럼) [확인됨: `config.py:51,75`]. setB 20,000 [확인됨: `config.py:33`].
- `/predict`·`/admin/reload` 라우트 [확인됨: `app.py:92,141`]. `/reset` 없음 [확인됨].
