# 핸드오프 검토 — H4-서빙 (레드팀, 실행 명세)

- **대상**: `docs/design/h4/serving/handoff.md` (초안)
- **대상 commit**: `0e123e9`
- **검토일**: 2026-06-28
- **핵심 질문**: train-serving skew 없이 **causal**하게 서빙되고, 인프라가 **자립적**이며, **게이트가 작동**하는가.
- **판정**: ⛔ **HOLD 2건 → 구현 금지.** bit-동일·상태동치·frozen-only·번들 원자성 게이트는 잘 설계됨. 막히는 곳: **(1) API 입력 계약이 결측→NaN을 강제하지 않아 0-fill/required skew 진입로**, **(2) 환자별 상태가 동시성·K8s replica에서 깨짐(causal 약속 무력화)**.

---

## PASS

- **H4s-a #2 bit-동일** — 스트리밍 전처리 == 배치(`missing→normalize`) ±1e-6(`:58`). 배치 기준은 H2-c `transform`(ffill→fill_mean→clip→normalize, 동결 상수)(`h2c:65-67`)로 명확, 프로그래매틱. PASS *(결측 케이스 보강은 HOLD-1·권고).*
- **H4s-a #3 상태동치** — stateful 1스텝 == 전체 재입력 ±1e-6(`:59`). 단방향 GRU(`gru.py` `bidirectional=False`)에서 정확히 성립(GRU 점화식). 현재 `forward`가 `h_n` 버림(`gru.py:33`) → stateful forward 추가 명시(`:54`). eval-mode·전층 h_n 명시. PASS.
- **H4s-a #1 번들 원자성** — 동일 run_id 출처 검증(`:49,57`). GRU run이 단일 번들(state_dict+npz+json)이라 자연 성립. PASS.
- **H4s-a #5 frozen-only** — 서빙 경로 fit/tune/select/compute_* 미호출 AST grep(`:61`, H3-b 선례). PASS *(grep 대상=serve/*.py 명시는 권고).*
- **H4s-b causal/스키마** — 시뮬레이터 t에 1..t만(`:72`), 스키마 run featureset 파생(9/18, `:71,77`)이라 불일치 차원 불가. B 재생은 frozen-only라 구조적으로 관찰 전용. PASS.
- **H4s-c 인프라 PASS 프로그래매틱** — `docker build` + `kubectl apply --dry-run=client`(`:96-97`). PASS *(YAML 인라인·pdm 문구는 권고).*
- **정합** — run 번들 ↔ H2 MLflow(H3-b 로드 선례), 입력분포 메트릭(`:73`)이 covariate 드리프트 토대. PASS.

---

## HOLD (수정 필요)

### HOLD-1 — API 입력 계약이 "결측→NaN"을 강제하지 않음 (★ skew 진입로)

- **항목**: H4s-b `app.py` 스키마(`:71`), H4s-a 전처리(`:51-52`), PASS #2
- **문제**: 서빙의 skew는 **API 입력에서 들어온다**. 전처리는 결측을 NaN으로 받아 ffill→fill(A평균) 해야 하는데(0-fill 금지, `:19,52`), pydantic 스키마(`:71`)가 **각 피처를 어떻게 받는지(필수? nullable? 누락 시 기본값?)** 미명세다. 만약 (a) 모든 필드 required → 클라가 "미측정"을 표현 못 함, (b) 누락→0 기본 → **0-fill = 금지 위반·skew**, (c) 누락→평균 → ffill 우회·skew. **"미측정 측정치"가 반드시 `NaN`으로 파이프라인에 들어가야** train과 동일.
- **근거**: `:71`(스키마 무명세), `:19,52`(0-fill 금지·ffill 전제), DDD 결정 2 "옵션 검사"(`h4_serving_decisions:44`). PASS #2(`:58`)는 *완전 관측* 시퀀스만 암시 — 결측 행 미검증.
- **제안**: app 스키마를 **피처별 `Optional[float] = None`**, 누락/`null`→**`np.nan`** 매핑(절대 0/평균 아님)으로 명문화. PASS #2에 **"일부 피처 결측 행(선두 결측·중간 결측 포함)을 스트리밍→배치와 bit-동일"** 케이스 추가(NaN 경로 검증, 0-fill 부재 확인).

### HOLD-2 — 환자별 상태가 동시성·K8s replica에서 깨짐 (causal 약속 무력화)

- **항목**: H4s-a `predictor.py`(`:54`), 결정 3 미결(`h4_serving_decisions:56`), H4s-c Deployment(`:90`), PASS #4(`:60`), 실패 모드(`:111-112`)
- **문제**: 서빙은 환자별 hidden state를 **"식별자별 dict/저장소"**(`:54`)로 든다. 두 가지가 미해결:
  1. **동시성**: FastAPI는 async — 같은 환자에 대한 동시 요청이 hidden state의 read-modify-write를 경쟁하면 상태 오염 → 조용히 틀린 causal 예측. PASS #4(`:60`)는 **순차 교차** 스트리밍만 본다(전역 상태 공유 버그는 잡지만 **동시 접근 레이스는 미검증**).
  2. **K8s replica**: Deployment(`:90`)가 replicas>1이면 **in-memory dict는 파드마다 분리** → 한 환자의 연속 요청이 다른 파드에 닿아 **상태 불연속**(causal 깨짐). 결정 3의 "메모리/Redis" 선택(`:56`)이 배포 토폴로지와 **미정합**.
- 실패 모드(`:112`)는 "환자 상태 혼선"을 *정지 트리거로 나열*하지만 **방지·검출 기법이 없다**.
- **근거**: `:54,90`; `h4_serving_decisions:56`; PASS #4(`:60`); 실패 모드(`:111-112`).
- **제안**: 상태관리를 **배포 토폴로지와 함께 결정** — (권장) **replicas=1 + 환자별 직렬화(per-pid lock/single-flight)** 로 프로토타입 명시, 또는 **공유 상태저장소(Redis)** 로 cross-replica 일관성 확보. PASS #4를 "**동시** 같은-환자 요청에도 상태 무결"로 강화. 실패 모드에 **상태저장소 장애·순서 어긋난 시점(out-of-order)·미등록 pid(콜드스타트)** 추가.

---

## 실행 전 권고 (비차단)

1. **인프라 YAML 실인라인** — `:88-93`이 manifests를 *서술*만 하고 실제 Dockerfile/YAML 본문이 없음. `:93` "pdm 패턴 **기억해** 인라인"은 외부 레포 의존을 함의(WORKFLOW §6 위배 소지). → **표준 K8s/Docker를 직접 인라인**(pdm 무관)하고 "pdm 기억" 문구 삭제. Deployment(probe→`/health`)·Service·ConfigMap·Dockerfile 골격을 핸드오프에 복붙.
2. **AST grep 대상 명시** — `serve/*.py`에 한정(`fit`·`tune`·`select_threshold`·`compute_norm_stats`·`compute_fill_mean` 미호출). `GRUm2m` *클래스* import는 허용, `gru.train_gru` 호출은 금지. (predictor가 train/gru.py를 import하므로 스코프 분리 필요.)
3. **bit-동일 #2 기준 고정** — 배치 기준 = **번들 동결 상수로 돌린 `transform`**(`h2c:65-67`와 동일 순서), 같은 환자에 스트리밍 vs 배치 ±1e-6. 케이스: 완전관측·선두결측·중간결측·전부결측 피처.
4. **frozen-only 런타임 보강** — bundle 통계·τ를 로드 후 **불변(immutable)** 취급, 서빙 어디서도 재대입 금지(권고적 assert).
5. **시뮬레이터 B 가드 명시** — B 재생 경로가 bundle/τ를 변형하지 않음(frozen-only)을 주석/assert로(이미 구조적이나 명문화).
6. **관측성** — 입력 피처 분포는 **per-feature 히스토그램/요약**(미관측률 포함)으로 노출해야 covariate 드리프트(H4-드리프트)가 실제로 올라감.

---

## 1차 확인 결과

- **stateful == 전체 재입력** [확인됨: `gru.py` 단방향 + GRU 점화식] — 단 `forward`가 h_n 버림(`:33`)이라 stateful forward 추가 필요(코드 추가, 결함 아님). eval-mode 필수.
- **ffill 스트리밍 == 배치** [확인됨: `missing.py:22-35`] — 상태 NaN 초기화·피처별 마지막관측 carry면 동일(HOLD-1은 *입력이 NaN으로 들어오는가*의 별개 문제).
- **번들 = 단일 run** [확인됨: H2 GRU run에 state_dict+npz+json 묶임, H3-b 로드 검증].
- ⚠️ **pdm-mlops 패턴**: CC 1차 확인 불가 — 핸드오프는 표준 기술로 자립 인라인해야(권고 1). 본 검토도 pdm 내용을 [확인됨]으로 올리지 않음.

---

## 다음 단계

**HOLD 2건(API 결측→NaN 계약 · 상태 동시성/replica) 해소 후 재검토.** 전부 PASS 전 구현(코드·디렉토리 생성) 금지(WORKFLOW §5·§6).

---

## 재검토 v2

- **대상**: `docs/design/h4/serving/handoff.md` v2 (개정 이력 v2 — HOLD 2 + 비차단)
- **검토일**: 2026-06-28
- **판정**: ✅ **PASS — HOLD 0건.** v1 HOLD 2건 해소, 비차단 반영, 신규 모순 없음. → **다음은 H4s-a 구현 착수.** (사소한 cosmetic nit 1건.)

### 회귀 검증 (요청 4항목)

**1. HOLD-1 (API 결측 계약) → ✅ 해소.**
- `app.py` 계약(`h4_serving_handoff:76`): "피처 `Optional[float] = None`. **누락·null → `np.nan`**(절대 0/평균 아님 — 0이면 0-fill 위반, 평균이면 ffill 우회 → 둘 다 skew)." skew 진입로 차단.
- PASS #2(`:63`): "**결측 행(선두·중간) 포함 케이스**로 검증, 0-fill 부재, 누락 입력이 np.nan으로 들어옴." 결측 경로가 게이트에 박힘. ✓

**2. HOLD-2 (상태 동시성/replica) → ✅ 해소.**
- predictor(`:59`): per-pid 직렬화(**per-pid lock**), 기본 **replicas=1**(in-memory), 확장 시 **Redis** 옵션 — 동시성·cross-replica 둘 다 다룸.
- PASS #4(`:65`): "두 환자 교차 **AND 같은 환자 동시 요청에도 hidden state 무결**(per-pid lock 검증). replicas=1 전제(또는 Redis)." 순차→동시로 강화.
- 배포 정합(`:95`): Deployment **replicas=1**(stateful in-memory 전제, 확장 Redis).
- 실패 모드(`:117-118`): "같은 환자 동시 요청 레이스 / 상태저장소 장애 / out-of-order 시점 도착 / 미등록 pid 요청 / replicas>1에서 in-memory 상태 분리" 전부 추가. ✓

**3. 비차단 → ✅ 반영.**
- 인프라 자립(`:98`): "표준 K8s 리소스로 **본 토막에서 실제 YAML 작성**, pdm 등 외부 레포 참조·인용 금지 — 표준 기술만으로 자립." "pdm 기억" 문구 삭제. (Dockerfile/Deployment(probe→`/health`)/Service/ConfigMap 스펙 구체 → CC가 표준 YAML 생성 가능, PASS는 docker build+kubectl dry-run으로 검증.)
- AST grep 대상(`:25`): `src/sepsis/serve/*.py` 명시.
- frozen immutable(`:23`): "로드 후 immutable".
- 입력분포(`:78`): "per-feature 히스토그램".

**4. 신규 모순 → 없음.**
- **replicas=1 ↔ 결정 8 K8s**: replicas=1도 정상 Deployment — 결정 8(Deployment/Service/ConfigMap)과 충돌 없음. 확장은 Redis로 문서화(프로토타입 범위 정합).
- **replicas=1 ↔ 번들 원자성**: 독립 관심사(상태 일관성 vs 단일 run 로드), 충돌 없음.
- **ConfigMap run 교체 ↔ replicas=1**: RUN 변경+재시작, 단일 파드가 새 번들 원자 로드. 정합.

### Cosmetic nit (비차단)
- `:5-6` "**개정 이력**" 헤더가 2줄 중복 — 한 줄 삭제(사소).

### 1차 확인 (v1, 변동 없음)
stateful==전체 재입력(단방향 GRU+점화식, `forward`가 h_n 버림→stateful forward 추가) · ffill 스트리밍==배치(`missing.py:22-35`) · 번들=단일 run(H3-b 로드) · pdm는 표준 기술로 자립 인라인 — [확인됨: 코드 대조].

**결론: HOLD 0 → H4s-a(`serve/bundle.py`·`preprocess_rt.py`·`predictor.py`) 구현 착수.** cosmetic nit 1건은 구현 중 흡수.
