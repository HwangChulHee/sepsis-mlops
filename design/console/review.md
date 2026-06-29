# Console DDD 레드팀 검토

- **대상**: `design/console/decisions.md` (v1 초안, 설계부)
- **대상 commit**: 35288ca (작업트리 기준)
- **검토일**: 2026-06-29
- **핵심 질문**: 콘솔이 노출하려는 H4 백엔드(alias swap/rollback, validate, MLflow 링크)가 *실제 코드*와 정합하며, "운영 일관성"이라는 핵심 주장이 코드로 지탱되는가?
- **판정**: **HOLD — blocker 2건** (라운드 1)

---

## PASS

- **결정 3 게이트 항목이 `ValidationResult` 실제 필드와 일치** — `bholdout_util/prauc`, `no_regression`, `cross_site_claim=False` 모두 존재(`src/sepsis/retrain/validate.py:31-43`). A-val 무회귀 `new_util >= old_util - eps(0.02)`도 코드와 일치(`validate.py:59`, `:46`). 각 모델이 자기 frozen stats로 채점하는 것도 확인(`validate.py:53-57`).
- **cross-site 정직성 플래그가 CLAUDE.md 평가 기조와 정합** — 재학습 검증은 A+B in-distribution이라 cross-site 일반화 주장이 아님을 코드가 명시(`validate.py:40-42`, `:8`). 콘솔이 이를 "통과/실패 아닌 정직성 플래그"로 표시하겠다는 것은 누수/과대주장 방지 관점에서 옳다.
- **결정 5 함수 참조 대체로 정확** — `active_version`(`deploy.py:46`, featureset 단위 맞음), `swap`이 `approved is not True`면 `PermissionError` raise(`deploy.py:59-60`), `rollback`(`deploy.py:68`), `set_alias` 원자 스왑 = `os.replace`(`bundle.py:38`) 모두 실재.
- **콘솔이 새 학습 누수를 만들지 않음** — 콘솔은 표시/운영 계층이고, 재학습 파이프라인은 환자 단위 B 분할(`pipeline.py:22-28`), train-only stats(`pipeline.py:72-77`), 0-fill 금지·ffill→train mean, mask OFF를 이미 지킨다. 콘솔 결정이 이 경로를 건드리지 않음.

---

## blocker

### B1. 결정 2/5 — 콘솔의 alias swap/rollback이 *실행 중인 서빙 앱에 전파되지 않는다* (핵심 일관성 주장 미지탱)

- **문제**: 결정 2는 "공유 자원 = 번들 저장소(active alias)"이고 "콘솔이 활성 alias 변경 → 서빙이 반영"되는 일관성을 검토 요청 항목으로 내세운다(`decisions.md:38,43`). 그러나 서빙 코드는 alias를 읽지도, 핫리로드하지도 않는다.
- **근거**:
  - `src/sepsis/serve/app.py:34-44` `state()` — 번들을 **모듈 전역 `_S`에 한 번 로드하고 영구 캐시**한다. 재로드 트리거가 없다.
  - 컨테이너 경로는 alias(`gru_<fs>` 심볼릭링크)가 아니라 **고정된 `SERVE_BUNDLE_DIR`(ConfigMap으로 주입된 특정 `$RUN` 디렉토리)** 를 읽는다(`app.py:38-40`). dev 경로는 MLflow h2c run을 읽는다(`app.py:42`). 어느 쪽도 `deploy/artifacts/gru_<fs>` alias를 읽지 않는다.
  - drift baseline도 동일하게 `_DS`에 영구 캐시(`app.py:97-110`).
  - 즉 콘솔이 `deploy.swap`/`deploy.rollback`으로 심볼릭링크를 바꿔도(`deploy.py:64,70`), 실행 중인 서빙 pod는 메모리에 든 옛 번들을 계속 쓴다. K8s에서 활성 버전 교체의 실제 메커니즘은 ConfigMap 변경 + 롤링 재시작이지 심볼릭링크 스왑이 아니다 — **두 메커니즘이 단절**되어 있다.
- **영향**: 성공 기준 "롤백 시 모델·전처리·τ·drift reference가 함께 복원됨"(`decisions.md:110`)이 *실행 중 서빙*에서는 성립하지 않는다. 콘솔의 핵심 가치(운영 일관성)가 코드로 지탱되지 않는다.
- **제안**: 전파 메커니즘을 명시적으로 결정하라 — (a) 서빙이 alias를 읽고 reload 엔드포인트/감시를 갖도록 하거나, (b) 콘솔 swap이 K8s 롤아웃(ConfigMap 갱신 + 재시작)을 트리거하는 구조로 정의하거나. 어느 쪽이든 결정 2가 전제한 "alias = 단일 조정점"이 실제 서빙 로딩 경로와 어떻게 연결되는지 DDD에 적어야 한다. (서빙 수정은 "범위 외/모델 만드는 건 콘솔 밖"과 충돌하므로 범위 재정의가 필요.)

> **[reviser 응답]** 해소: 결정 2에 **2-A 절** 신설(`decisions.md` 결정 2). (1) 활성 버전 단일 진실원천 = alias `gru_<fs>`로 통일, 서빙 버전 소스를 고정 `$RUN`→alias로 변경(서빙 1점을 범위에 명시 편입 — 범위표 v2 + "v2 범위 변경" 주석). (2) 전파를 *명시적 단계*로 결정: 프로덕션=K8s 롤링 재시작(콘솔 좁은 RBAC로 Deployment patch, 버전 교체 시 hidden state 리셋이 올바름을 명시), dev=`POST /admin/reload`로 `_S`/`_DS` 재초기화. (3) 예측 로직·환자 상태는 불변(분리 원칙 유지). 성공기준에 "swap/rollback이 실행 중 서빙에 실제 전파" 추가, 교차단계 의존(H4s)에 명시. **미해결로 남긴 것**: 프로덕션 전파를 롤링 재시작 vs in-place reload로 최종 택일하는 것과 정확한 K8s RBAC는 배포환경 종속 → `[검증 필요]`로 정직히 표기(두 경로 정의 + K8s 기본값 제안).

### B2. 결정 6 — 콘솔이 운영하는 *재학습 버전*에 MLflow 연결 키가 존재하지 않는다

- **문제**: 결정 6은 "실험·수치 상세는 콘솔에 재구현하지 않고 MLflow Tracking으로 링크"하며 연결 키 출처를 검토 요청 항목으로 둔다(`decisions.md:84-88`). 그러나 콘솔이 다루는 champion/challenger = 재학습 산출 버전에는 MLflow run 자체가 없다.
- **근거**:
  - `src/sepsis/retrain/pipeline.py` 전체에 `mlflow` import도, `start_run`도 없다. `RetrainResult`에 `run_id` 필드 없음(`pipeline.py:31-47`).
  - `deploy.materialize()`가 쓰는 `meta.json`에 `run_id`·git commit이 **없다** — `featureset, hp, input_dim, tau, version, trained_on`만 기록(`deploy.py:35-37`).
  - 따라서 `deploy/artifacts/gru_<fs>@<version>` 버전 디렉토리에서 MLflow run으로 이어줄 키가 없다. MLflow 실험에는 H2/H3 run만 있고 재학습 버전은 없다.
- **영향**: 콘솔의 운영 대상(재학습 champion/challenger)의 "어떻게 만들었나"(epochs, val_loss, B-split seed, train_pids)는 MLflow가 아니라 `RetrainResult`/`meta.json`에만 존재한다. 결정 6대로라면 이 버전들의 상세는 어디로도 링크되지 않는다 — 결정이 구현 불가.
- **제안**: 재학습 파이프라인이 MLflow run을 남기도록 하고 `materialize()`의 `meta.json`에 `run_id`(또는 git commit)를 기록하도록 결정에 포함하거나, 결정 6의 적용 범위를 "H2 베이스라인 버전 한정"으로 솔직히 좁히고 재학습 버전의 출처는 콘솔/감사가 직접 보유한다고 명시하라.

> **[reviser 응답]** 해소: 결정 6에 **6-A 절** 신설(`decisions.md` 결정 6). (1) 연결 키 = `meta.json.run_id`(+`git_commit`) 버전당 1개로 확정, 콘솔이 이를 읽어 deep-link 생성. (2) H4r 재학습/자재화 경로가 MLflow run 로깅 + `meta.json`에 run_id·git_commit 기록하도록 보강 요구 — 콘솔 밖 코드 변경이라 **교차단계 의존**으로 명시(`[검증 필요]`). (3) **정직한 폴백**: run_id 없으면 죽은 링크 대신 meta.json 직접 표시 + 재학습 상세(epochs/val_loss/seed/train_pids)를 승인 시 감사 스냅샷으로 캡처(결정 4 확장) → 출처 공백 제거. 성공기준·교차단계 의존(H4r)에 반영. 레드팀이 준 두 선택지(보강 / 범위 축소)를 **둘 다** 채택: 보강을 요구하되 미선행 시 폴백으로 구현 가능하게 닫음.

---

## major

- **M1. 결정 4 `[확인됨: 메모리·코드]`가 거짓** — `decisions.md:64`는 "레포가 이미 SQLAlchemy 사용 [확인됨]"이라 하나, `src/` 전체에 `sqlalchemy`/`create_engine`/`declarative_base` 사용처가 **0건**(grep 결과). MLflow가 의존성으로 SQLAlchemy를 끌어올 뿐 프로젝트 코드는 쓰지 않는다. WORKFLOW의 검토 1차 목표("[확인됨]이 진짜 확인됐는지")에 정면으로 걸리는 허위 출처 등급. → `[검증 필요]` 또는 `[우리 결정]`으로 강등하고 근거를 다시 대라.

> **[reviser 응답]** 해소: 허위 `[확인됨: 메모리·코드]`를 취소선 처리하고 **SQLAlchemy ORM 채택 = 신규 의존 도입 `[우리 결정]`** 으로 강등(`decisions.md` 결정 4). grep 0건을 본문에 명시. 대신 실재하는 것만 `[확인됨]`으로: sqlite 파일 DB 패턴은 `serve/bundle.py:115`의 `sqlite:///mlflow.db`로 존재(직접 확인). "sqlite 저장"과 "SQLAlchemy ORM 계층"을 구분해 후자가 신규임을 명확화. "의료 규제 직결"도 *구조 정렬*로 한정(M4 연계).
- **M2. 결정 5 — `rollback`이 승인·검증을 우회하고 자체 감사도 없다** — 성공 기준은 "승인 없이는 교체 불가"·"모든 롤백이 감사에 기록됨"(`decisions.md:109,111`). 그러나 `deploy.rollback`은 approval/validation 체크 없이 곧장 `set_alias`만 호출하며 감사 훅이 없다(`deploy.py:68-70`). 롤백도 활성 버전을 바꾸는 "교체"다. 콘솔이 롤백에 승인을 요구할지, 감사를 어디서 강제할지 미결. → DDD가 롤백의 승인/감사 정책을 명시적으로 결정해야 함.

> **[reviser 응답]** 해소: 결정 5에 **5-A 절** 신설(`decisions.md` 결정 5). 롤백 = **승인 게이트 + 감사 필수**로 결정. validation 재검증은 *의도적 면제*(롤백 대상은 과거 검증·승인된 champion이라 재검증이 인시던트 복구를 막는 역효과). 강제 지점을 **콘솔 API 경계**로 확정(백엔드 deploy.rollback엔 훅이 없으므로 API가 호출 전 승인 확인+감사 기록 강제, swap도 동일). 방어 심화로 `deploy.rollback`에 `approved` 가드·prev 반환 대칭화를 H4r 교차단계 권고로 남김(`[검증 필요]`). 성공기준에 반영.
- **M3. 결정 5 — `swap`은 `no_regression=False`여도 raise한다(이중 게이트 누락 설명)** — `deploy.py:61-62`는 `validation.no_regression`이 False면 사람이 승인해도 `ValueError`. 결정 5/성공기준은 `approved` 게이트만 언급(`decisions.md:75,109`). 즉 REGRESSED 버전은 "승인" 버튼을 눌러도 교체 실패한다. 승인 UI가 이 경우(REGRESSED → 승인 불가/우회 정책)를 어떻게 다룰지 결정에 빠져 있다.

> **[reviser 응답]** 해소: 결정 3에 두 게이트 성격 구분 추가(`decisions.md` 결정 3). **하드 게이트 = A-val 무회귀 하나**(REGRESSED = 비승격, 승인해도 backend `ValueError`)임을 명시, **콘솔 UI 귀결**로 REGRESSED 후보는 승인 버튼 비활성 + 사유 표시, 사람 승인은 PASS 후보에만 활성. 백엔드에 우회 경로 없음을 명시(향후 오버라이드는 `deploy.swap` 코드 변경 = 범위 밖 `[검증 필요]`). 결정 5의 swap 시그니처 설명도 두 raise(PermissionError/ValueError)를 모두 명시하도록 수정. 성공기준에 M3 항목 추가.
- **M4. 결정 4 — 감사의 "행위자(actor)" 출처가 미정인데 의료 규제 감사로 정당화** — `decisions.md:61,66`은 행위자를 기록한다 하고 그 출처를 검토 요청 항목으로만 남겼다. 인증/식별 메커니즘이 범위(`decisions.md:15-21`)에 전혀 없다. 인증 없는 actor 필드는 자유 텍스트 주장일 뿐이라 "의료 규제 직결" 근거를 스스로 약화시킨다. → 범위 내 인증을 둘지, 아니면 "actor는 미검증 입력"임을 솔직히 명시할지 결정 필요.

> **[reviser 응답]** 해소: 결정 4에 actor 출처 정직화(`decisions.md` 결정 4) + 범위표 v2에 "인증/SSO·신원 검증 = 범위 외" 명시. MVP에서 `actor`는 **운영자 제출 미검증 입력**임을 명시, 스키마에 `actor_unverified`로 표기하고 어디서도 "검증된 신원"으로 주장하지 않음. 향후 SSO/OIDC용 `verified_subject` 컬럼 예약(MVP null). "의료 규제 직결" 주장을 *감사 구조 정렬*로 완화하고 검증된 귀속은 후속 과제(`[검증 필요]`)로 정직히 남김. 인증을 범위에 넣지 않은 이유 = MVP 규모상 SSO 통합은 별도 과제이며, 거짓 보안 주장보다 미검증 명시가 낫다는 판단(사람 게이트에서 인증 범위 편입 여부 최종 판단 요청).

---

## minor

- **m1.** `swap` 시그니처는 `validation`·`approved`가 keyword-only(`deploy.py:55`, `*` 뒤). `decisions.md:75`는 위치 인자처럼 적었다 — API 매핑 시 키워드 호출 필요. 구현 명세에 반영.
  > **[reviser 응답]** 해소: 결정 5의 swap 시그니처를 `swap(featureset, version_dir, *, validation, approved)`로 정정 + "keyword-only이므로 API는 키워드로 호출" 명시(`decisions.md` 결정 5).
- **m2.** 결정 3 — B-holdout 성능에는 통과 임계값이 없다(informational). 실제 게이트 판정(PASS/REGRESSED)은 `no_regression` 하나뿐(`validate.py:59`, `deploy.py:61`). "판정 보조 신호"가 B-holdout이 아니라 A-val 무회귀에만 결부됨을 표시 설계에서 혼동하지 말 것.
  > **[reviser 응답]** 해소: 결정 3에 "B-holdout = informational(임계값 없음), 하드 게이트 = A-val 무회귀 하나"를 명시, 콘솔이 B-holdout을 게이트가 아니라 참고 수치로 표시하도록 결정(`decisions.md` 결정 3, M3과 함께).
- **m3.** 결정 1 — 백엔드에 "challenger" 개념이 없다. `deploy.py`는 active alias(champion)와 버전 디렉토리만 안다. challenger/archived 구분 기준을 콘솔이 어디서 도출할지 미정.
  > **[reviser 응답]** 해소: 결정 1에 champion/challenger/archived 도출 규칙 추가 — champion=`active_version`, challenger=validation 있는 비활성 `gru_<fs>@<v>`, archived=감사 DB의 과거 활성 이력(파일시스템에 표식 없으므로 감사가 source of truth)(`decisions.md` 결정 1).

---

# 라운드 2 — 재검증 (v2 대상)

- **판정**: **HOLD — blocker 1건 (신규 N1)**
- **라운드 1 blocker 해소 여부**: B1(alias 전파) **해소** / B2(MLflow 연결 키) **해소(근본 부분)** — 단 폴백의 재학습 상세 캡처 주장은 N1과 겹쳐 미성립, 잔여를 N1으로 흡수.
- **major 4건(M1~M4) 전부 해소 확인**, minor도 반영 확인.

## blocker

### N1 (신규). 콘솔 데이터 모델·핵심 액션이 영속화되지 않는 in-memory 결과에 의존 — 백엔드는 version dir에 게이트결과·재학습상세를 저장하지 않는다

- **문제**: v2는 보완 과정에서 콘솔이 *시간 분리된 materialized 디렉토리*를 관찰·조작한다는 모델을 굳혔다(결정 1 challenger 도출, 결정 2 "콘솔 stateless·재학습 로직 콘솔 밖"). 그런데 콘솔이 의존하는 핵심 데이터가 디스크에 존재하지 않는다.
- **근거** (코드 직접 확인):
  - version dir에 영속되는 유일 산출물 = `meta.json` = `{featureset, hp, input_dim, tau, version, trained_on}` 뿐(`deploy.py:35-37`). `ValidationResult`·`RetrainResult`를 디스크로 쓰는 코드는 **레포 전체에 0건** — `validate()`는 in-memory dataclass 반환(`validate.py:46,60`), `materialize()`는 검증결과·epochs/val_loss/train_pids 미기록(`deploy.py:28-43`).
  - **결정 5 핵심 액션 자체가 막힌다**: `deploy.swap(...)`은 `validation` 인자를 요구하고 `getattr(validation,"no_regression")`을 본다(`deploy.py:55,61`). 콘솔이 materialized dir만 보는 시점엔 `ValidationResult`가 사라졌고, 재구성하려면 in-memory `RetrainResult`가 필요한데 디스크엔 `model.pt/pre.npz/meta.json`뿐이라 **재구성 불가**. DDD 본인도 미해결 검토요청 항목으로 남김(`decisions.md:118`).
  - **결정 1 challenger 도출 불능**: "validation 결과가 있으나 비활성인 버전"을 판별할 디스크 표식이 없음.
  - **결정 4 게이트 스냅샷 / 결정 6-A 폴백 공허**: "승인 시점 게이트 결과/재학습 상세 스냅샷"은 *승인 시점에 그 데이터가 손에 있다*고 가정하나 그 시점엔 없다 → 성공기준(`decisions.md:162` 출처 공백 없이 추적)이 폴백으로도 안 닫힘.
- **왜 blocker**: 교차단계 의존 목록에 run_id/git_commit·alias/reload·rollback 가드는 있으나 **"ValidationResult·재학습상세를 version dir에 영속"이 빠져 있다.** 이게 없으면 콘솔 중심 동작(승인→swap)과 데이터 모델 3개(challenger·게이트 스냅샷·재학습 폴백)를 구현할 수 없다.
- **제안**: 교차단계 의존(H4r)에 **"`materialize()`(또는 validate 직후)가 version dir에 `validation.json`(no_regression·B-holdout/A-val 수치)과 재학습 메타(epochs·val_loss·seed·train_pids 요약·run_id·git_commit)를 영속한다"**를 추가하고, 콘솔 swap 호출 시 `validation`을 그 파일에서 복원하는 경로를 결정 5에 명시하라. 이로써 B2 폴백·challenger 도출·게이트 스냅샷이 동시에 성립한다.

> **[reviser 응답]** 해소: **결정 5-B 신설**(`decisions.md` 결정 5)로 in-memory 의존을 근본 제거. (1) 교차단계 의존 H4r에 `gru_<fs>@<v>/validation.json`(ValidationResult 전체: no_regression·B-holdout/A-val 수치·eps·cross_site_claim·검증시각)과 `retrain.json`(epochs·val_loss·b_split_seed·train_pids 요약·b_retrain/b_holdout 개수·run_id·git_commit) **영속을 추가** — 콘솔 밖 코드 변경이라 `[검증 필요]`. (2) **swap 복원 경로**: 콘솔 API가 version dir의 `validation.json`을 읽어 `SimpleNamespace`로 복원→`deploy.swap(..., validation=객체, approved=True)`. `deploy.swap`이 `getattr(validation,"no_regression",...)`(`deploy.py:61`)로 속성 접근하므로 dict가 아닌 객체 래핑 필요 — **백엔드 시그니처 변경 없이** 콘솔 한 줄로 충족. (3) **자재화·검증 순서 고정**(retrain→materialize→validate→json 기록), `validation.json` 없는 dir은 결정 1대로 *미완성 후보*(승인 배제). (4) 이로써 결정 1 challenger 디스크 표식(`decisions.md` 결정 1) / 결정 4 게이트 스냅샷 출처=영속 파일(`decisions.md` 결정 4) / 결정 6-A 폴백 실데이터화(`decisions.md` 6-A)가 **동시에 닫힘**. **코드 현실 명시**: `RetrainResult`에 `seed`/`run_id`/`git_commit` 필드가 현재 없으므로(`pipeline.py:31-47`) H4r 보강이 이를 추가/주입해야 함을 결정 5-B·교차단계 의존에 `[검증 필요]`로 정직히 표기. 미선행 시 거짓 승인 대신 *미완성 후보 배제*로 공백을 닫음(덮지 않음).

## major (라운드 2)

- **MJ1. alias 가변화로 `_S`/`_DS` 분리 lazy-load 스큐 위험** — 고정 `$RUN`→가변 alias로 바꾸면 `_S`(첫 `/predict`)·`_DS`(첫 `/drift`)가 다른 시점에 alias를 읽어 모델↔reference 스큐(거짓 드리프트). `load_bundle_from_dir`는 3파일 비원자적 로드라 로드 도중 swap 시 혼합 번들 위험. → 구현 명세에서 "reload는 alias-타겟을 1회 해석해 `_S`·`_DS`를 함께 로드, 로드 중 swap 배제"로 못박을 것.

  > **[reviser 응답]** 해소: 결정 2-A에 reload 원자성 규약 추가(`decisions.md` 결정 2-A). (i) reload는 alias 타겟 1회 해석→고정 버전 dir에서 `_S`·`_DS` 동시(동일 버전) 로드, (ii) 로드 중 swap 배제(alias 갱신→그 다음 reload 직렬화), (iii) 첫 요청 lazy-load도 같은 해석 시점 쓰도록 eager 로드/공유 해석 캐시. 더해 **프로덕션 롤링 재시작 경로는 새 pod가 부팅 시 alias 1회 해석·fresh 로드라 스큐가 구조적으로 없음**을 명시 — 규약은 in-place reload 경로에만 필요. 서빙 코드 변경이라 `[검증 필요]`.

- **MJ2. `meta.json`에 `run_id` 없으면 `/health` run_id가 alias명으로 오염** — `load_bundle_from_dir`는 `meta.get("run_id", str(d.name))`(`bundle.py:102`). 재학습 번들 meta엔 run_id 없음 → `/health` run_id="gru_vitals"로 무의미. B2 보강이 이 표면 식별까지 닫는지 확인 필요.

  > **[reviser 응답]** 해소: 결정 6-A에 5번 항 추가(`decisions.md` 6-A). `bundle.py:102`의 `meta.get("run_id", str(d.name))` 폴백을 직접 인용하고, B2의 H4r 보강(`meta.json`에 `run_id`·`git_commit` 기록)이 **이 표면 식별까지 동시에 해소**함을 명시 — run_id가 채워지면 `/health`도 실제 run_id 보고. H4r 코드 변경이라 `[검증 필요]`.

## minor (라운드 2)

- **mn1. archived 콜드스타트** — archived 출처=감사 DB. 빈 1일차엔 과거 champion이 안 보임. 초기 시드/마이그레이션 정책 한 줄 필요.

  > **[reviser 응답]** 해소: 결정 1에 archived 콜드스타트 시드 정책 추가(`decisions.md` 결정 1). 부트스트랩 시 현재 champion을 seed 감사 1건(action=`bootstrap`, actor=`system`)으로 기록해 출처를 감사에 남김. 콘솔 *이전* 과거 swap 이력은 파일시스템에 표식이 없어 소급 복원 불가 → 1일차 archived가 비어 있음을 UI에 명시(거짓 복원 금지). 이후 교체/롤백이 누적되며 채워짐. 성공기준에도 반영.

- **mn2. 결정 6-A 폴백 문구 과대** — "감사+meta가 출처 보유"는 N1 해소 전까지 meta.json 4필드만 실제 보유. N1 영속화 결정 후 문구를 한정.

  > **[reviser 응답]** 해소: 결정 6-A 3번 항에 "문구 한정(mn2)" 명시(`decisions.md` 6-A). N1 영속 *이전*엔 meta.json 4필드뿐이라 "감사+meta가 출처 보유"가 과대였음을 인정하고, 폴백이 실데이터를 갖는 것은 **결정 5-B의 `validation.json`/`retrain.json` 영속이 선행될 때 한정**으로 수정. 영속 미선행 버전은 *미완성 후보*로 승인 배제 → 출처 공백 있는 버전은 운영 대상에서 *배제*하는 방식으로 닫음(덮지 않음). 폴백 출처를 "감사+meta"→"version dir의 validation.json/retrain.json + meta.json(+승인 시 감사 사본)"으로 정정.

---

# 라운드 3 — 최종 재검증 (v3 대상)

- **판정**: **PASS — blocker 0건** ✅ 루프 정상 종료, 구현 명세부 진입 가능.
- **N1 해소 여부**: **해소** — (a) 5-B 복원 경로가 `deploy.swap`의 `getattr(validation,"no_regression",False)` 계약(`deploy.py:55,61`)과 정합, 백엔드 시그니처 변경 없이 디스크만으로 swap 재구성 성립(getattr 기본값 False라 속성 부재 시 ValueError로 fail-safe). (b) `RetrainResult`에 seed/run_id/git_commit 부재(`pipeline.py:31-47`)를 회피 없이 H4r 교차단계 의존으로 정직히 표기(`seed`는 `retrain(seed=42)` 인자로 실재 → b_split_seed 기록 실현 가능). (c) 폴백이 validation.json 없는 dir을 *미완성 후보*로 **승인 배제**해 출처 공백을 말이 아니라 대상 집합 제한으로 실제로 닫음.
- **MJ1**(reload 원자성): 결정 2-A에 (i)alias 1회 해석·(ii)로드 중 swap 배제·(iii)eager 공유 캐시로 규약화, 서빙 코드 변경이라 `[검증 필요]`로 명세부 이관 — 설계 게이트에서 수용.
- **MJ2**(run_id 표면 오염): `bundle.py:102` `meta.get("run_id", str(d.name))` 인용, H4r meta.json run_id 기록이 `/health` 식별까지 동시 해소 — 정합 확인.
- **신규 blocker**: 없음.
- **누수 4종**: 무영향 — v3 변경분이 디스크 영속/표시 계층에 한정, 재학습 파이프라인의 환자단위 분할·train-only stats·0-fill 금지·mask OFF 불변.

## minor (명세부 정리 권고 — 단독으로 막지 않음)

- **mn-a. challenger 표식이 validation.json 단독에 묶여 retrain.json 부분쓰기에 취약** — 미완성 후보 판정을 validation.json 하나에만 거는데(`decisions.md:39,182`), 6-A 폴백은 approvable 버전에 retrain.json도 있다고 가정. 부분쓰기(validation.json 기록 후 retrain.json 실패) 시 "승인 가능하나 재학습 출처 공백" 재발 가능. → 미완성 후보 판정을 **두 파일 모두**에 걸거나 두 파일 원자적 기록(temp→rename)을 명세부에 못박을 것.
- **mn-b. `eps`·"검증 시각"은 `ValidationResult` 필드가 아님** — `eps`는 `validate(*, eps=0.02)` 인자일 뿐 dataclass 필드 아니고(`validate.py:46`) 타임스탬프 필드도 없음(`validate.py:31-43`). "전체 직렬화"만으론 안 나오니 영속 시점에 별도 주입함을 명세부에 명시. (swap-임계 필드 no_regression엔 영향 없음.)
- **mn-c. swap 반환값 `prev` 캡처 미명시** — `deploy.swap`은 이전 활성 버전명을 반환(`deploy.py:57-58,63-65`). 5-A 롤백이 `deploy.rollback(fs, previous_version_name)`을 호출하려면 이 `prev`가 감사에 기록돼야 롤백 대상이 결정됨. "swap 반환 prev를 감사 레코드에 캡처" 명시 권고.

---

## 루프 종결 요약

- **3라운드 만에 PASS** (blocker: 2 → 1(신규 N1) → 0).
- 라운드별 커밋: `130e097`(round 1), `09cc7f9`(round 2). 라운드 3은 redteam PASS만(보완 불필요).
- 잔여 minor 3건(mn-a/b/c)은 구현 명세부(핸드오프)에서 정리 권고 — blocker 아님.
- **사람 게이트 대기**: 푸시는 사람 승인 후.
