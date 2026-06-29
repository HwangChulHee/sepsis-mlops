# Console-Prep 설계결정문서 (DDD) — H4 백엔드 보강

> **설계 근거**: 운영 콘솔(`design/console/decisions.md`, 2회 검토 통과)이 의존하는 H4 백엔드(`retrain/`·`serve/`) 선행 보강. 콘솔 검토에서 드러난 [검증 필요] 교차단계 의존을 실제로 닫는다. 콘솔 구현 *전에* 완료돼야 콘솔 명세가 구현 가능해진다.
> **워크플로우·출처등급**: [`WORKFLOW.md`](../WORKFLOW.md). 검토(`review.md`) 통과 후 명세부로.
> **상태**: 초안 (설계부) — 레드팀 검토 전.
> **범위 주의**: FS↔감사 DB 일관성(콘솔 재검토 결정 7)은 **콘솔 명세부 몫**(콘솔이 swap+감사를 동시 변이할 때의 트랜잭션 문제)이라 여기서 제외. 본 문서는 순수 백엔드 보강만.

## 한 줄 요약

콘솔이 운영하려면 재학습 산출물이 **디스크에 충분히 영속**돼야 하고(`validation.json`·`retrain.json`·`run_id`), 서빙이 **활성 alias를 읽고 reload**해야 한다. 대부분 *이미 `RetrainResult`에 있는 값을 디스크에 기록*하는 일이고, 신규는 `run_id`·`git_commit`·MLflow 로깅·alias reload 경로다.

## 범위 / 범위 외

| 범위 (console-prep) | 범위 외 |
|---|---|
| `materialize`가 `validation.json` 영속 | FS↔감사 DB 일관성 (콘솔 명세부) |
| `materialize`가 `retrain.json` 영속 | 콘솔 API·UI (콘솔 작업) |
| `run_id`·`git_commit` 생성 + MLflow run 로깅 | 재학습 알고리즘 변경 |
| `meta.json`에 `run_id` 기록 | 드리프트 로직 |
| 서빙이 alias 읽고 reload | |
| reload 원자성(`_S`·`_DS` 동시) | |

---

## 결정 1: `materialize`가 `validation.json`을 영속한다

- **결정**: `materialize`(또는 그 직후)가 version dir에 `validation.json`을 쓴다 — `ValidationResult` 전체(`bholdout_util/prauc`·`no_regression`·`cross_site_claim`·`eps`·검증시각). 콘솔 swap이 이 파일에서 `validation`을 복원해 `deploy.swap(..., validation=객체)`에 넘긴다.
- **흐름(콘솔 의존)**: 콘솔 승인 → `swap`은 `validation` 인자 필요(`deploy.py:55,61` `getattr(validation,"no_regression")`) → 그 시점 in-memory `ValidationResult`는 없음 → **디스크에 없으면 swap 불가**(N1).
- **근거 + 출처등급**:
  - `validate()`는 in-memory dataclass 반환, 디스크 기록 없음 [확인됨: validate.py:46,60].
  - `eps`·검증시각은 `ValidationResult` 필드가 아님 — 영속 시 별도 주입 필요 [확인됨: validate.py:31-43].
- **검토 요청**: 영속 시점(materialize 내부 vs validate 직후)과 자재화·검증 순서(retrain→materialize→validate→기록).

---

## 결정 2: `materialize`가 `retrain.json`을 영속한다

- **결정**: version dir에 `retrain.json`을 쓴다 — `epochs`·`val_loss`·`b_split_seed`·`train_pids` 요약·`b_retrain`/`b_holdout` 개수·`run_id`·`git_commit`. 콘솔이 재학습 상세를 표시·감사 스냅샷에 사용.
- **흐름(콘솔 의존)**: 콘솔 결정 6-A(재학습 버전 상세) + 결정 1(challenger 도출) + 결정 4(게이트 스냅샷)가 이 파일에 의존.
- **근거 + 출처등급**:
  - `epochs`·`val_loss`·`train_pids`·`b_retrain`·`b_holdout`은 **이미 `RetrainResult`에 존재** [확인됨: pipeline.py:42-47] → 기록만 추가.
  - `seed`는 `retrain(seed=42)` 인자로 존재 [확인됨: pipeline.py:50] → `b_split_seed`로 기록 가능.
  - `run_id`·`git_commit`만 신규(결정 3).
  - 현재 `meta.json`은 `featureset/hp/input_dim/tau/version/trained_on`만 기록 [확인됨: deploy.py:35-37].
- **검토 요청**: `retrain.json`과 `meta.json`의 역할 분리(meta=서빙용, retrain=감사용)인지, 중복인지.

---

## 결정 3: `run_id`·`git_commit` 생성 + MLflow run 로깅

- **결정**: 재학습 파이프라인이 MLflow run을 남기고, `run_id`와 `git_commit`을 `RetrainResult`(또는 materialize 인자)에 주입한다. 콘솔이 이 `run_id`로 MLflow deep-link 생성.
- **흐름(콘솔 의존)**: 콘솔 결정 6(실험 수치 MLflow 링크) → 재학습 버전에 run이 없으면 링크 불가(B2).
- **근거 + 출처등급**:
  - `pipeline.py`에 `mlflow` import·`start_run` 없음, `RetrainResult`에 `run_id`/`git_commit` 필드 없음 [확인됨: pipeline.py 전체, :31-47].
- **정직한 폴백**: MLflow 로깅이 미선행이면 — 죽은 링크 대신 `retrain.json` 직접 표시(결정 2). 콘솔이 폴백으로 동작 가능.
- **검토 요청**: `git_commit` 취득 방법(런타임 `git rev-parse`)이 재현성을 해치지 않는지.

---

## 결정 4: `meta.json`에 `run_id` 기록 (서빙 식별)

- **결정**: `materialize`의 `meta.json`에 `run_id`를 추가한다.
- **흐름**: `load_bundle_from_dir`가 `meta.get("run_id", str(d.name))` 폴백 → 재학습 번들엔 run_id 없어 `/health` run_id가 alias명("gru_vitals")으로 오염(MJ2) [확인됨: bundle.py:102].
- **근거 + 출처등급**: 결정 3이 `run_id`를 만들면 여기 기록만 추가.
- **검토 요청**: 결정 2의 `retrain.json` run_id와 `meta.json` run_id 중복 — 단일 출처로 통일할지.

---

## 결정 5: 서빙이 활성 alias를 읽고 reload한다

- **결정**: 서빙의 번들 소스를 고정 `$RUN`(ConfigMap 특정 dir)에서 **활성 alias `gru_<fs>`**로 통일한다. 전파 경로 = **프로덕션: K8s 롤링 재시작**(콘솔이 좁은 RBAC로 Deployment patch, 버전 교체 시 hidden state 리셋이 올바름), **dev: `POST /admin/reload`**로 `_S`/`_DS` 재초기화. 예측 로직·환자 상태 격리는 불변.
- **흐름(콘솔 의존)**: 콘솔 swap/rollback이 alias 변경 → 서빙이 안 읽으면 실행 중 pod는 옛 번들 유지(B1). 콘솔 핵심 가치(운영 일관성) 미지탱.
- **근거 + 출처등급**:
  - `state()`가 `_S`에 한 번 로드하고 영구 캐시, 재로드 트리거 없음 [확인됨: serve/app.py:34-44].
  - 컨테이너는 고정 `SERVE_BUNDLE_DIR` 읽지 alias 안 읽음 [확인됨: app.py:38-40].
- **검토 요청(설계 판단)**: 프로덕션 전파를 롤링 재시작 vs in-place reload 중 무엇을 기본으로 — 콘솔 결정 2-A는 롤링 재시작 기본 + dev reload로 정함. 이 방향 유지 여부. (정확한 K8s RBAC는 배포환경 종속 `[검증 필요]`.)

---

## 결정 6: reload 원자성 (`_S`·`_DS` 동시 로드)

- **결정**: in-place reload 경로에서 — alias 타겟을 1회 해석해 `_S`(서빙 번들)·`_DS`(drift reference)를 **동일 버전으로 동시 로드**, 로드 중 swap 배제(직렬화), 첫 요청 lazy-load도 같은 해석 시점 사용(eager/공유 해석 캐시). 프로덕션 롤링 재시작 경로는 새 pod가 부팅 시 alias 1회 해석·fresh 로드라 스큐 구조적으로 없음 → 규약은 in-place reload에만 필요.
- **흐름**: 고정 `$RUN`→가변 alias로 바뀌면 `_S`(첫 `/predict`)·`_DS`(첫 `/drift`)가 다른 시점에 alias 읽어 모델↔reference 스큐(거짓 드리프트) [확인됨: app.py:34-44,97-110]. `load_bundle_from_dir`는 3파일 비원자 로드.
- **근거 + 출처등급**: 위 코드 [확인됨].
- **검토 요청**: 구체적 락/직렬화 *기법*은 명세부 — 설계부에선 "동시 로드 + 로드 중 swap 배제"라는 의존·결정까지.

---

## 만들 것 / 안 만들 것

**만듦**: 결정 1~6 (retrain 영속 4 + serve reload 2).
**재활용**: `RetrainResult`의 기존 필드(epochs·val_loss·train_pids 등), `deploy.materialize` 골격.
**안 만듦**: FS↔감사 DB 일관성(콘솔 명세부), 콘솔 API/UI(콘솔 작업).

## 성공 기준 (초안 — 검토 후 확정)

- version dir에서 `validation.json`을 읽어 `deploy.swap`에 넘기는 경로가 성립(콘솔 swap 가능).
- 재학습 버전에 `run_id`가 있어 MLflow 링크 또는 `retrain.json` 폴백 동작.
- `validation.json` 없는 dir은 콘솔이 미완성 후보로 배제(출처 공백을 덮지 않음).
- 콘솔 swap/rollback이 실행 중 서빙에 실제 전파됨(롤링 재시작 or reload).
- reload 시 `_S`·`_DS`가 동일 버전(스큐 없음).
- **누수 불변**: 재학습 영속화가 환자 단위 B 분할·train-only stats·0-fill 금지·mask OFF를 건드리지 않음.

> **구현 명세부(어떻게)는 설계부 검토 통과 후 이어붙인다** — 직렬화 포맷, 락 기법, MLflow run 구조, `/admin/reload` 시그니처.