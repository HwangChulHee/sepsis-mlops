# Console-Prep 설계결정문서 (DDD) — H4 백엔드 보강

> **설계 근거**: 운영 콘솔(`docs/design/console/decisions.md`, 2회 검토 통과)이 의존하는 H4 백엔드(`retrain/`·`serve/`) 선행 보강. 콘솔 검토에서 드러난 [검증 필요] 교차단계 의존을 실제로 닫는다. 콘솔 구현 *전에* 완료돼야 콘솔 명세가 구현 가능해진다.
> **워크플로우·출처등급**: [`WORKFLOW.md`](../WORKFLOW.md). 검토(`review.md`) 통과 후 명세부로.
> **상태**: 설계부 v2 — 레드팀 라운드 1 반영(B1 두 파일 원자 co-visible 영속 = 결정 7 신설, MJ1 seed 출처 코드현실 교정, MJ2 전파확인 식별자 일치, mn1~4). 명세부는 blocker 0 확정 후.
> **범위 주의**: FS↔감사 DB 일관성(콘솔 재검토 결정 7)은 **콘솔 명세부 몫**(콘솔이 swap+감사를 동시 변이할 때의 트랜잭션 문제)이라 여기서 제외. 본 문서는 순수 백엔드 보강만.

## 한 줄 요약

콘솔이 운영하려면 재학습 산출물이 **디스크에 충분히 영속**돼야 하고(`validation.json`·`retrain.json`·`run_id`), 서빙이 **활성 alias를 읽고 reload**해야 한다. 대부분 *이미 `RetrainResult`에 있는 값을 디스크에 기록*하는 일이고, 신규는 `run_id`·`git_commit`·`seed` 주입(셋 다 영속 지점 미도달 — MJ1)·MLflow 로깅·alias reload 경로다. 두 영속 파일(`validation.json`·`retrain.json`)은 **원자·co-visible**하게 기록한다(결정 7).

## 범위 / 범위 외

| 범위 (console-prep) | 범위 외 |
|---|---|
| `materialize`가 `validation.json` 영속 | FS↔감사 DB 일관성 (콘솔 명세부) |
| `materialize`가 `retrain.json` 영속 | 콘솔 API·UI (콘솔 작업) |
| 두 파일 원자·co-visible 영속(완성=AND, 결정 7) | 재학습 알고리즘 변경 |
| `run_id`·`git_commit`·`seed` 생성·주입 + MLflow run 로깅 | |
| `meta.json`에 `run_id` 기록 | 드리프트 로직 |
| 서빙이 alias 읽고 reload | |
| reload 원자성(`_S`·`_DS` 동시) | |

---

## 결정 1: `materialize`가 `validation.json`을 영속한다

- **결정**: `materialize`(또는 그 직후)가 version dir에 `validation.json`을 쓴다 — `ValidationResult` 전체(`no_regression`(하드 게이트)·`bholdout_util/prauc`·`new_aval_util`/`old_aval_util`/`new_aval_prauc`/`old_aval_prauc`(콘솔 결정 3 무회귀 헤드라인 수치, validate.py:34-38)·`cross_site_claim`·`eps`·검증시각). 콘솔 swap이 이 파일에서 `validation`을 복원해 `deploy.swap(..., validation=객체)`에 넘긴다. **이 파일은 단독으로 완성 표식이 아니다 — `retrain.json`과 AND로 함께 가시화돼야 한다(결정 7).**
- **흐름(콘솔 의존)**: 콘솔 승인 → `swap`은 `validation` 인자 필요(`deploy.py:55,61` `getattr(validation,"no_regression")`) → 그 시점 in-memory `ValidationResult`는 없음 → **디스크에 없으면 swap 불가**(N1).
- **근거 + 출처등급**:
  - `validate()`는 in-memory dataclass 반환, 디스크 기록 없음 [확인됨: validate.py:46,60].
  - `eps`·검증시각은 `ValidationResult` 필드가 아님 — 영속 시 별도 주입 필요 [확인됨: validate.py:31-43].
- **검토 요청**: 영속 시점(materialize 내부 vs validate 직후)과 자재화·검증 순서(retrain→materialize→validate→기록).

---

## 결정 2: `materialize`가 `retrain.json`을 영속한다

- **결정**: version dir에 `retrain.json`을 쓴다 — `epochs`·`val_loss`·`b_split_seed`·`train_pids` 요약·`b_retrain`/`b_holdout` 개수·`run_id`·`git_commit`. 콘솔이 재학습 상세를 표시·감사 스냅샷에 사용. **이 파일도 단독 완성 표식이 아니다 — `validation.json`과 AND로 함께 가시화돼야 한다(결정 7).**
- **흐름(콘솔 의존)**: 콘솔 결정 6-A(재학습 버전 상세) + 결정 1(challenger 도출) + 결정 4(게이트 스냅샷)가 이 파일에 의존.
- **근거 + 출처등급**:
  - `epochs`·`val_loss`(pipeline.py:45-46)·`train_pids`·`b_retrain`·`b_holdout`(pipeline.py:39-41)은 **이미 `RetrainResult`에 존재** [확인됨: pipeline.py:39-47] → 기록만 추가.
  - `seed` (출처 교정 — MJ1): `seed`는 `retrain(*, seed=42)`의 **함수 인자일 뿐 `RetrainResult` 필드가 아니다** [확인됨: pipeline.py:31-47에 seed 필드 없음]. 영속 지점 `materialize(retrain_result, version)`은 `retrain_result`만 받으므로 [확인됨: deploy.py:28] **seed에 도달할 수 없다** → "인자로 존재하니 기록 가능"이 아니라, `run_id`·`git_commit`과 **동일하게 결정 3으로 `RetrainResult`에 추가하거나 `materialize` 인자로 주입해야** `b_split_seed`로 영속된다. (콘솔 결정 5-B line 137과 일치.)
  - `run_id`·`git_commit`·`seed` 3종 모두 신규 주입(결정 3).
  - 현재 `meta.json`은 `featureset/hp/input_dim/tau/version/trained_on`만 기록 [확인됨: deploy.py:35-37].
- **검토 요청**: `retrain.json`과 `meta.json`의 역할 분리(meta=서빙용, retrain=감사용)인지, 중복인지.

---

## 결정 3: `run_id`·`git_commit`·`seed` 생성·주입 + MLflow run 로깅

- **결정**: 재학습 파이프라인이 MLflow run을 남기고, **`run_id`·`git_commit`·`seed` 3종**을 `RetrainResult`(또는 materialize 인자)에 주입한다. 콘솔이 이 `run_id`로 MLflow deep-link 생성, `seed`는 `retrain.json`의 `b_split_seed` 출처(MJ1 — 영속 지점에 도달시키는 결정).
- **흐름(콘솔 의존)**: 콘솔 결정 6(실험 수치 MLflow 링크) → 재학습 버전에 run이 없으면 링크 불가(B2). `seed`는 영속 지점 미도달이라 결정 2가 이 주입에 의존(MJ1).
- **근거 + 출처등급**:
  - `pipeline.py`에 `mlflow` import·`start_run` 없음, `RetrainResult`에 `run_id`/`git_commit`/`seed` 필드 없음 [확인됨: pipeline.py 전체, :31-47]. `seed`는 `retrain()` 인자로만 존재(:50)해 결과 객체·materialize에 전달되지 않음 → 세 값 모두 동일하게 주입 대상.
- **정직한 폴백**: MLflow 로깅이 미선행이면 — 죽은 링크 대신 `retrain.json` 직접 표시(결정 2). 콘솔이 폴백으로 동작 가능.
- **deploy.rollback 대칭화 (mn4b — 교차참조)**: `deploy.rollback`의 `approved` 가드·prev 반환 대칭화 권고(deploy.py:68-70 prev 미반환)는 **콘솔 문서가 이미 권고로 보유**(콘솔 line 219·결정 5-A·7-3). 콘솔이 사전 `active_version` 읽기로 우회하므로 비블로킹이며, console-prep은 순수 백엔드 *영속* 보강 범위라 여기서 재결정하지 않고 교차참조만 남긴다.
- **검토 요청**: `git_commit` 취득 방법(런타임 `git rev-parse`)이 재현성을 해치지 않는지.

---

## 결정 4: `meta.json`에 `run_id` 기록 (서빙 식별)

- **결정**: `materialize`의 `meta.json`에 `run_id`를 추가한다.
- **흐름**: `load_bundle_from_dir`가 `meta.get("run_id", str(d.name))` 폴백 → 재학습 번들엔 run_id 없어 `/health` run_id가 alias명("gru_vitals")으로 오염 [확인됨: bundle.py:102].
- **단일 출처 확정 (mn2)**: MLflow deep-link **연결 키의 단일 출처 = `meta.json.run_id`**(콘솔 결정 6-A와 일치). 결정 2의 `retrain.json` run_id는 **감사 스냅샷용 사본**이지 연결 키가 아니다 — 두 곳에 같은 값이 들어가되 권위는 `meta.json.run_id` 하나로 못 박는다.
- **전파 확인의 비교 대상 — 식별자 네임스페이스 일치 (MJ2)**: 콘솔의 전파 성공 확인(콘솔 결정 2-A line 66 / 성공기준 line 206)은 두 값이 **다른 네임스페이스**임을 주의해야 한다 — `active_version()`은 `os.readlink`로 *버전 디렉토리명*(`gru_vitals@<v>`)을 반환하고 [확인됨: deploy.py:46-48], `/health.run_id`는 *`meta.json`의 run_id 해시*를 반환한다 [확인됨: app.py:80]. **전파 확인 = `/health.run_id`를 *현재 alias가 가리키는 타겟 버전 dir의 `meta.json.run_id`*와 비교**(active_version 문자열이 아님). 콘솔 결정 2-A의 "active_version과 일치" 문구도 이 의미(현재 alias 타겟 dir의 `meta.json.run_id`)로 읽힌다. 이로써 사슬 마지막 홉(서빙이 새 alias를 실제로 따라왔는지)의 식별자 미스매치가 닫힌다.
- **근거 + 출처등급**: 결정 3이 `run_id`를 만들면 여기 기록만 추가.
- **검토 요청**: 결정 2의 `retrain.json` run_id와 `meta.json` run_id 중복 — 단일 출처(meta.json)로 통일됨(mn2 반영).

---

## 결정 5: 서빙이 활성 alias를 읽고 reload한다

- **결정**: 서빙의 번들 소스를 고정 `$RUN`(ConfigMap 특정 dir)에서 **활성 alias `gru_<fs>`**로 통일한다. 전파 경로 = **프로덕션: K8s 롤링 재시작**(콘솔이 좁은 RBAC로 Deployment patch, 버전 교체 시 hidden state 리셋이 올바름), **dev: `POST /admin/reload`**로 `_S`/`_DS` 재초기화. 예측 로직·환자 상태 격리는 불변.
- **흐름(콘솔 의존)**: 콘솔 swap/rollback이 alias 변경 → 서빙이 안 읽으면 실행 중 pod는 옛 번들 유지(B1). 콘솔 핵심 가치(운영 일관성) 미지탱.
- **근거 + 출처등급**:
  - `state()`가 `_S`에 한 번 로드하고 영구 캐시, 재로드 트리거 없음 [확인됨: serve/app.py:34-44].
  - 컨테이너는 고정 `SERVE_BUNDLE_DIR` 읽지 alias 안 읽음 [확인됨: app.py:38-40].
- **superseded 명시 (mn3)**: 현재 dev 경로 `load_bundle(SERVE_FEATURESET)`(= MLflow h2 experiment by featureset, app.py:42 / bundle.py:105-138)는 본 alias 통일로 **대체(superseded)** 된다 — dev·컨테이너 양쪽이 같은 활성 표식(alias)을 보게 하는 의도된 통일이다. 기존 dev MLflow 폴백이 사라짐을 명시해 구현 혼선을 막는다(어느 환경이든 활성 소스 = alias 단일).
- **검토 요청(설계 판단)**: 프로덕션 전파를 롤링 재시작 vs in-place reload 중 무엇을 기본으로 — 콘솔 결정 2-A는 롤링 재시작 기본 + dev reload로 정함. 이 방향 유지 여부. (정확한 K8s RBAC는 배포환경 종속 `[검증 필요]`.)

---

## 결정 6: reload 원자성 (`_S`·`_DS` 동시 로드)

- **결정**: in-place reload 경로에서 — alias 타겟을 1회 해석해 `_S`(서빙 번들)·`_DS`(drift reference)를 **동일 버전으로 동시 로드**, 로드 중 swap 배제(직렬화), 첫 요청 lazy-load도 같은 해석 시점 사용(eager/공유 해석 캐시). 프로덕션 롤링 재시작 경로는 새 pod가 부팅 시 alias 1회 해석·fresh 로드라 스큐 구조적으로 없음 → 규약은 in-place reload에만 필요.
- **흐름**: 고정 `$RUN`→가변 alias로 바뀌면 `_S`(첫 `/predict`)·`_DS`(첫 `/drift`)가 다른 시점에 alias 읽어 모델↔reference 스큐(거짓 드리프트) [확인됨: app.py:34-44,97-110]. `load_bundle_from_dir`는 3파일 비원자 로드.
- **근거 + 출처등급**: 위 코드 [확인됨].
- **검토 요청**: 구체적 락/직렬화 *기법*은 명세부 — 설계부에선 "동시 로드 + 로드 중 swap 배제"라는 의존·결정까지.

---

## 결정 7: `validation.json`·`retrain.json`의 원자·co-visible 영속 (완성 표식 = 두 파일 AND) — B1

- **레드팀이 드러낸 단절** [확인됨: 코드 + 콘솔 의존]: 콘솔은 challenger 판별·감사 스냅샷 무결성을 **"두 파일이 원자적으로 함께 가시화된다(부분/torn write 없음)"** 전제 위에 세운다(콘솔 결정 1 line 40, 5-B/mn-a line 139, 교차단계 의존 line 218). 그런데 결정 1·2는 `validation.json`·`retrain.json`을 *독립* 결정으로만 두어, 둘의 원자 co-visible 기록도, torn-read 방지도, "두 파일 AND가 완성 표식"이라는 결정도 없었다.
- **영속 타이밍이 구조적으로 갈린다** [확인됨: 코드]: `retrain.json` 데이터는 `materialize(retrain_result, version)` 시점에 `RetrainResult`에 이미 있어 **일찍** 쓸 수 있다(epochs/val_loss/train_pids 등, pipeline.py:39-47). `validation.json`은 `validate()`가 끝난 **이후에만** 가능하다(validate.py:46, materialize 이후 실행). → "둘 중 하나만 있는" 창이 구조적으로 생기고, 부분쓰기(둘 다 존재하나 하나가 torn) 시 콘솔이 깨진 JSON을 감사 스냅샷·swap 복원에 쓸 수 있다.
- **결정 (의존 식별 — 백엔드 계약으로 못 박음)** [우리 결정]:
  1. **완성 표식 = `validation.json` AND `retrain.json`.** 콘솔이 버전을 challenger로 인지하려면 version dir에 두 파일이 *모두* 있어야 한다. 둘 중 하나라도 없거나 부분/torn write된 dir은 **미완성 후보**(승인 불가)로 분류한다 — `validation.json`만 있고 `retrain.json` 누락("승인 가능하나 재학습 출처 공백")도, 그 역도 막는다.
  2. **두 파일은 한 커밋 단위로 원자·co-visible하게 가시화**한다 — "둘 다 완전히 보이거나, 둘 다 아직 안 보이거나"의 두 상태만 콘솔에 노출되고, "하나만/torn으로 보이는" 중간 상태는 노출되지 않아야 한다. 이는 콘솔이 백엔드(H4r)에 거는 **교차단계 의존**이다.
- **흐름(콘솔 의존)**: 콘솔 결정 1(challenger = 두 파일 AND)·결정 4(게이트 스냅샷 무결성)·결정 5-B(mn-a)가 이 원자 영속에 의존. 미선행 시 해당 버전은 두 파일 AND 미충족으로 *미완성 후보*로만 보이고 승인 불가(거짓 승인 금지).
- **설계부 깊이 — 기법은 명세부 위임** [우리 결정]: "두 파일이 원자·완전하게 영속돼야 한다는 의존·완성 표식(AND)·torn-read 배제"까지가 설계부 몫이다. **원자 가시화의 구체 기법(temp 경로에 쓴 뒤 `os.replace` rename·fsync 순서·디렉토리 커밋 단위·완성 마커 파일 등)은 구현 명세부에서 확정**한다. 본 설계부는 *기법이 아니라 의존의 존재*를 못 박는다.
- **검토 요청**: 두 파일 AND 완성 표식이 콘솔 challenger 판별·감사 무결성 전제를 실제로 지탱하는지, 원자 가시화 의존이 H4r 교차단계 요구로 충분히 식별됐는지.

---

## 만들 것 / 안 만들 것

**만듦**: 결정 1~7 (retrain 영속 4 + serve reload 2 + 두 파일 원자 co-visible 영속 1).
**재활용**: `RetrainResult`의 기존 필드(epochs·val_loss·train_pids 등), `deploy.materialize` 골격.
**안 만듦**: FS↔감사 DB 일관성(콘솔 명세부), 콘솔 API/UI(콘솔 작업).

## 성공 기준 (초안 — 검토 후 확정)

- version dir에서 `validation.json`을 읽어 `deploy.swap`에 넘기는 경로가 성립(콘솔 swap 가능).
- 재학습 버전에 `run_id`가 있어 MLflow 링크 또는 `retrain.json` 폴백 동작.
- **`validation.json`·`retrain.json`이 두 파일 AND로 함께 가시화될 때만** 콘솔이 challenger로 인지 — 하나라도 없거나 부분/torn write된 dir은 *미완성 후보*로 배제(출처 공백을 덮지 않음, B1/결정 7).
- 두 파일은 **원자·co-visible하게 영속**돼 "하나만/torn으로 보이는" 중간 상태가 콘솔에 노출되지 않음(B1/결정 7, 기법은 명세부).
- `b_split_seed`가 영속 지점에 실제 도달함 — `seed`가 `RetrainResult`/`materialize`로 주입돼 `retrain.json`에 기록됨(MJ1).
- 전파 확인이 식별자 네임스페이스 일치로 닫힘 — `/health.run_id`를 *현재 alias 타겟 dir의 `meta.json.run_id`*와 비교(active_version 문자열 아님, MJ2).
- 콘솔 swap/rollback이 실행 중 서빙에 실제 전파됨(롤링 재시작 or reload).
- reload 시 `_S`·`_DS`가 동일 버전(스큐 없음).
- **누수 불변**: 재학습 영속화가 환자 단위 B 분할·train-only stats·0-fill 금지·mask OFF를 건드리지 않음.

> **구현 명세부(어떻게)는 설계부 검토 통과 후 이어붙인다** — 직렬화 포맷, **두 파일 원자 가시화 기법(temp→rename·fsync 순서·디렉토리 커밋 단위)**, 락 기법, MLflow run 구조, `/admin/reload` 시그니처.