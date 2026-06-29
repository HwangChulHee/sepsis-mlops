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

---

# 라운드 4 — 새 기준 재검토 (흐름추적 + 검토깊이)

- 대상: design/console/decisions.md (v3, 라운드 3에서 PASS 받음)
- 검토일: 2026-06-29
- 핵심 질문: 승인 → alias 전환 → 감사 기록 → 서빙 전파의 전체 사슬이 끊김·stale·경합 없이 닫히는가. "처리/기록/복원한다"가 실제 메커니즘까지 명세됐는가.
- 판정: HOLD — blocker 1건(동시성 + 크래시 원자성 2면). major 2건.
- 이전 PASS 재평가: N1 보완(5-B 디스크 영속)은 in-memory 의존은 제거했으나 구멍을 "FS alias ↔ 감사 DB 이중쓰기 일관성"으로 옮겼고, 그 새 구멍이 닫혀 있지 않다. 즉 라운드 3 PASS는 흐름의 마지막 마디(전파·기록 원자성)를 보지 않은 통과였다.

## PASS (재확인)
- N1 복원 객체 ↔ deploy.swap 계약 정합 — swap은 getattr(validation,"no_regression",False)(deploy.py:61)로 속성 접근, 부재 시 기본 False→ValueError fail-safe. SimpleNamespace 복원으로 백엔드 시그니처 변경 없이 성립.
- swap keyword-only 시그니처 정확 — swap(featureset, version_dir, *, validation, approved)(deploy.py:55).
- set_alias 단일 op 원자성 — os.replace(tmp, link)(bundle.py:38)는 심볼릭링크 교체 그 자체는 원자적. (단, blocker는 "swap+감사" 복합 연산의 원자성 문제로 별개.)
- 게이트 필드 ↔ ValidationResult 일치 / 누수 무영향 — validate.py:31-43, 재학습 파이프라인 환자단위 분할·train-only stats(pipeline.py:22-28,72-77) 불변.

## blocker
### B-new. 승인/롤백이 트랜잭션 경계 없는 다중 저장소 변이 — 동시성 제어·크래시 복구 둘 다 미결정
- 문제: 콘솔 승인 핸들러의 선언된 사슬 = ① validation.json 읽어 복원 → ② deploy.swap(FS alias 전환, prev 반환) → ③ 감사 레코드 기록(게이트 스냅샷·prev) → ④ 서빙 reload 트리거. 이 4단계는 하나의 원자 트랜잭션이 아니고, 직렬화도 안 된다.
- (면 1) 크래시 원자성 / FS·DB 분기 [확인됨: 코드 + DDD 공백]: ②와 ③ 사이 크래시 → alias는 V2를 가리키는데 감사엔 swap 기록이 없다. 결정 1은 archived = 감사 DB의 swap/rollback 이력을 source of truth로 선언(decisions.md:40)했는데, 그 권위 저장소가 실제 alias 상태와 어긋난다 → V1이 archived로 안 보이고, 게이트 스냅샷도 유실. 순서를 ③→② 로 뒤집어도 "기록은 있는데 swap 안 됨" 창이 생긴다. 어느 쪽이 권위인지·재기동 시 reconciliation을 어떻게 하는지 결정이 없다. deploy.rollback은 prev를 반환조차 안 한다(deploy.py:68-70).
- (면 2) 동시 승인 2건 — 직렬화 부재 [확인됨: 코드]: DDD의 "콘솔 stateless·1개면 충분"(decisions.md:48)은 scale-out 얘기지 요청 동시성이 아니다. FastAPI는 한 프로세스에서 요청을 동시 처리하므로 운영자 2명이 서로 다른 challenger를 동시 승인 가능. 둘 다 prev = active_version()을 V1으로 읽고, A는 V2로 B는 V3로 swap. alias는 last-writer-wins로 V3에 안착하지만 감사엔 "V1→V2"와 "V1→V3" 두 레코드가 prev=V1로 남는다. archived 도출·롤백 대상 결정이 오염된다. read-active→swap→audit를 감싸는 락/직렬화가 없다.
- 왜 blocker: 설계부는 기법(mutex vs DB 트랜잭션 vs advisory lock)까진 요구하지 않지만, "승인/롤백은 FS+DB에 걸친 트랜잭션이며 직렬화·복구 전략이 필요하다"는 의존 식별은 설계부 몫이다. 그게 둘 다 빠졌다. N1 보완이 audit-as-source-of-truth를 못 박은 결과 이 구멍이 확정됐다.
- 제안: ① 승인/롤백을 단일 직렬화 경계(전역 락 또는 동등)로 정의해 read-active→swap→audit를 원자 구간으로 묶을 것. ② FS alias와 감사 DB가 분기했을 때 어느 쪽이 권위이고 재기동 시 어떻게 화해하는지(예: 부트스트랩 시 실제 alias로 감사 보정) 결정할 것. 기법 디테일은 명세부로 넘기되 경계·권위·복구의 존재는 설계부에서 못 박아야 한다.

> **[reviser 응답]** 해소: **결정 7 신설**(`decisions.md` 결정 7)로 두 면을 모두 못 박음.
> - (면 2) **직렬화 경계** — 승인·롤백을 featureset 단위 단일 임계 구간으로 정의: `read-active(prev) → swap/set_alias → audit write`를 하나의 직렬화 구간으로 묶어 동시 승인의 last-writer-wins·prev 중복(V1→V2/V1→V3) 경합을 차단(결정 7-1). 직렬화 키=featureset(alias가 featureset 단위, `deploy.py:46`). 콘솔 scale-out 시 프로세스-로컬 락으론 부족하므로 공유 저장소 기반 락(DB advisory lock 등)이 필요함을 *의존으로 식별* — 결정 2의 "1개면 충분"을 "프로세스 내 직렬화가 닫힐 때의 전제, scale-out 시 공유 락으로 승격"으로 한정(결정 2 본문 + 결정 7-1). 기법(mutex/advisory lock/파일 lock)은 명세부.
> - (면 1) **권위·크래시 화해** — FS alias = *현재 활성 상태*의 권위(결정 7-2). 감사 DB는 *이력*의 출처지 현재 활성의 출처가 아님으로 결정 1의 "archived=감사 이력"을 *과거 활성 도출*로 한정(현재 champion은 언제나 `active_version` alias로 읽음 — 결정 1 champion 정의와 일치). ②후 ③전 크래시로 분기 시 **alias가 이긴다**. **재기동 화해(reconciliation)**: 부트스트랩 시 실제 alias와 감사 최신 활성 레코드를 대조, 어긋나면 보정 감사(action=`reconcile`, actor=`system`)를 기록해 감사를 실제 상태로 맞춤(결정 1 bootstrap seed 정책을 화해까지 확장). 게이트 스냅샷은 version dir의 `validation.json`에서 재읽기 복원되므로(결정 5-B) 유실 없음. 순서는 임계 구간 내 swap→audit, ③ 실패는 재기동 화해가 마지막 안전망(③→② 역순은 alias 권위 원칙과 충돌해 불채택).
> - **prev 캡처(mn-c 동반)** — swap 반환 prev와 롤백 시 사전 읽은 active_version을 임계 구간 안에서 읽어 감사에 캡처(결정 7-3 + 5-A). `deploy.rollback`이 prev 미반환(`deploy.py:68-70`)이므로 롤백 prev는 콘솔이 호출 *전* active_version으로 읽어 캡처. 기법은 명세부.

## major
### MJ-new1. 전파의 마지막 홉(reload 트리거) 실패·확인 부재 — 사슬이 조용히 끊길 수 있음
- 문제: 2-A는 swap 후 reload(롤링 재시작 | /admin/reload)를 명시적 단계로 둔다(decisions.md:59-61). 그러나 트리거 실패 시(K8s API 거부·/admin/reload 500·네트워크 단절) alias·감사는 갱신됐는데 서빙은 옛 _S/_DS를 계속 쓴다. 성공기준 "옛 번들 잔존 없음"(decisions.md:180)이 조용히 위반.
- 제안: 전파 성공을 확인(예: /health의 run_id가 새 버전과 일치)하고 실패 시 재시도/경보하는 의존을 식별. 기법은 명세부.

> **[reviser 응답]** 해소: 결정 2-A에 **전파 확인·실패 처리** 절 추가(`decisions.md` 결정 2-A). (a) 전파 성공 확인 = `/health` run_id가 새 버전과 일치하는지 폴링(MJ2로 `/health`가 실제 run_id 보고함이 전제), (b) 실패 시 재시도·운영자 경보, (c) 확인 전까지 콘솔 UI는 해당 버전을 "활성(전파 확인됨)"이 아니라 "전파 대기/실패"로 구분 표시 — alias·감사는 권위(결정 7)라 이미 갱신됐고 미전파는 *가시적 상태*로 노출(조용한 끊김 방지). 성공기준의 "옛 번들 잔존 없음"을 "전파 확인으로 검증, 미확인 시 명시 표시"로 보강. 기법은 명세부, 서빙 `/health` 의존이라 [검증 필요].

### MJ-new2. "백엔드 ValueError = 독립 방어선" 주장이 부정확 — 같은 파일의 단일 출처를 두 번 읽음
- 문제: M3는 REGRESSED 비승격을 "UI 버튼 비활성 + 백엔드 ValueError"의 이중 게이트로 못 박았고(decisions.md:79-81), 5-A는 swap의 ValueError를 "감사원이 아닌 방어선"이라 부른다(decisions.md:119). 그러나 N1 이후 콘솔이 validation.json을 읽어 객체를 복원해 swap에 건네주고, swap은 그 콘솔-복원 객체의 no_regression만 본다(deploy.py:61). UI도 백엔드도 같은 validation.json 파생이다. validation.json이 stale/오염되면 백엔드가 못 잡는다.
- 제안: "백엔드 ValueError는 콘솔 로직 누락(가드 드롭)은 막지만, validation.json 자체가 틀리면 못 막는다 — 둘은 같은 출처를 읽으므로 진정한 다출처 방어가 아니다"로 정직화. validation.json을 신뢰경계로 명시.

> **[reviser 응답]** 해소: 결정 3 "콘솔 UI 귀결"에 **방어선 성격 정직화(MJ-new2)** 추가(`decisions.md` 결정 3) + 5-A 문구 수정. "이중 게이트"는 *진정한 다출처 방어가 아님*을 명시: UI 버튼 비활성과 백엔드 `ValueError`는 **둘 다 같은 `validation.json` 파생**이라, 백엔드 가드는 *콘솔 로직 누락(가드 드롭)* 은 막지만 `validation.json` 자체가 stale/오염이면 못 막는다. 따라서 **`validation.json` = 신뢰경계**로 명시(mn-new2 동반: MVP는 이 경계를 신뢰하며, 직접 편집 시 하드 게이트 우회 가능함을 수용 가정으로 명기). 방어 깊이의 정확한 정의 = 가드 드롭 방지(코드 경로) ≠ 데이터 무결성 방어.

## minor
- mn-a (이월). challenger 표식이 validation.json 단독(decisions.md:39)이라 validation.json 기록 후 retrain.json 실패 시 "승인 가능하나 재학습 출처 공백" 재발. 미완성 후보 판정을 두 파일 모두에 걸거나 원자 기록(temp→rename)을 명세부에 못 박을 것.
  > **[reviser 응답]** 해소: 결정 1 challenger 판별을 **`validation.json` AND `retrain.json` 둘 다 존재**로 보강(`decisions.md` 결정 1) + 5-B에 두 파일 원자 기록(temp→rename, 또는 둘을 한 디렉토리 커밋으로) 요구를 명세부 이관 의존으로 명시. 부분쓰기 버전은 *미완성 후보*로 승인 배제.
- mn-b (이월). eps·"검증 시각"은 ValidationResult 필드가 아님(validate.py:31-43; eps는 validate(*, eps=) 인자). 5-B의 "전체 직렬화"만으론 안 나오니 영속 시 주입 명시.
  > **[reviser 응답]** 해소: 결정 5-B에 "`eps`·검증 시각은 `ValidationResult` 필드가 아니라 `validate(*, eps=)` 인자·외부 시계이므로 영속 시점에 **별도 주입**"을 명시(`decisions.md` 5-B). swap-임계 필드 `no_regression`엔 영향 없음을 함께 명기.
- mn-c (이월). deploy.swap의 반환 prev와 롤백 시 active_version 사전 읽기를 감사에 캡처함을 명시.
  > **[reviser 응답]** 해소: 결정 7-3에 명시(B-new 응답 참조) — 임계 구간 내에서 읽은 prev/active_version을 감사에 캡처.
- mn-new1. 롤링 재시작 중에는 새/옛 pod가 두 모델 버전을 동시 서빙(graceful drain). 의료 맥락에서 이 transient 혼재 허용 여부 한 줄 명시 권고(decisions.md:60).
  > **[reviser 응답]** 해소: 결정 2-A 롤링 재시작 항에 한 줄 추가(`decisions.md` 2-A) — drain 동안 새/옛 pod가 두 버전을 transient 동시 서빙하나, **환자별 추론은 stateless 요청 단위(hidden state는 요청 내 시퀀스로 재구성)이고 두 버전 모두 검증·승인된 번들**이라 의료적으로 허용 가능 범위로 수용. 무중단이 핵심이면 in-place reload 경로 택일 가능(이미 2-A에 정의). 단정 아닌 수용 판단이라 [우리 결정].
- mn-new2. validation.json 직접 편집하면 하드 게이트 우회 가능 = 파일이 신뢰경계. MVP 수용이라도 그 가정 명시.
  > **[reviser 응답]** 해소: MJ-new2 응답과 함께 결정 3에 명시(`decisions.md` 결정 3) — `validation.json` = 신뢰경계, 직접 편집 시 우회 가능함을 MVP 수용 가정으로 명기(인증·파일 무결성 검증은 후속 과제 [검증 필요]).

## 판정
HOLD — blocker 1건(B-new, 동시성+크래시원자성 2면). blocker 0이 아니므로 명세부 진입 불가.

---

# 라운드 5 — 결정 7 + 보완 재흐름추적

- 대상: design/console/decisions.md (v4)
- 대상 commit: 7a705c9 (reviser 보완)
- 검토일: 2026-06-29
- 핵심 질문: 결정 7(직렬화 경계·alias 권위·재기동 화해)이 승인→swap→감사→전파 사슬을 실제로 닫았는가, 아니면 구멍을 옮겼는가(reconcile 경합·부트스트랩 중 승인·/health 폴링 race). 새 결정이 기존 결정과 모순되지 않는가.
- 판정: HOLD — blocker 1건(B-r5), major 1건, minor 1건. 라운드 4 B-new의 코어(승인↔승인 동시성, ②후③전 크래시)는 닫혔으나, 보완이 새 쓰기 주체(reconcile·bootstrap-seed)를 직렬화 경계 밖에 남겨 동일 버그-클래스를 부분적으로 재생성했다.

## PASS (라운드 4 blocker B-new 코어 — 실제 닫힘 확인)
- B-new 면2(동시 승인 직렬화) 닫힘 — 결정 7-1이 read-active(prev)→swap/set_alias→audit write를 featureset 단위 단일 임계구간으로 묶음(decisions.md:173). active_version이 락 없는 readlink(deploy.py:46-48)임을 직접 확인 — 임계구간으로 감싸야 prev 갈라짐 차단. 직렬화 키=featureset도 alias 단위와 일치. scale-out 시 공유락 승격을 의존으로 식별(결정 2와 짝, decisions.md:50,173).
- B-new 면1(크래시 원자성)의 권위 정의 닫힘 — 결정 7-2가 현재 활성 권위=FS alias, 감사=이력 권위로 분리(decisions.md:174). 결정 1(decisions.md:39,41)·결정 2 면2(decisions.md:50)와 모순 없이 일치. 순서 swap→audit + ③실패를 재기동 화해가 받침(decisions.md:176)도 일관.
- prev 캡처 코드 정합(mn-c) — 결정 7-3(decisions.md:177)이 deploy.swap 반환 prev(deploy.py:63-65)와 롤백 사전 active_version 읽기(rollback은 prev 미반환, deploy.py:68-70)를 구분해 캡처.
- 결정 7 코드 인용 전부 정확 / 누수 무영향(pipeline.py:22-28 등 불변).

## blocker
### B-r5. reconcile·bootstrap-seed가 직렬화 경계 밖의 제3 쓰기 주체 — "archived 도출 오염 없음" 주장이 무방비 인터리브에서 거짓
- 항목: 결정 7-2 재기동 화해 / 결정 1 부트스트랩 seed의 경계 귀속.
- 문제: 결정 7-1의 직렬화 임계구간은 명시적으로 "승인·롤백"에만 한정(decisions.md:173). 그러나 reconcile(action=reconcile)과 bootstrap-seed(action=bootstrap)도 같은 권위쌍(alias 읽기 + 감사 쓰기)에 쓰는 주체인데(decisions.md:42,175) 이 경계 밖에 있다. 결정 7-2는 "게이트 스냅샷은 validation.json에서 복원되므로 archived 도출이 오염되지 않는다"고 단언(decisions.md:175). 이 단언은 reconcile/seed가 승인과 상호배제된다는 전제 위에서만 참인데, 그 전제가 결정 어디에도 못 박혀 있지 않다.
- 근거(구체 반례): 부트스트랩 reconcile이 alias=X·감사최종=Y(X≠Y, ②후③전 크래시 흔적)를 읽고 reconcile(prev=Y, target=X)를 쓰려는 순간, 동시 승인이 임계구간에서 active=X를 읽어 X→Z로 swap하고 audit(prev=X, target=Z)를 먼저 커밋(alias=Z). reconcile이 뒤늦게 쓰면 감사 이력 말미가 [..., swap X→Z, reconcile target=X]가 되어 감사상 최종 활성=X이지만 실제 alias=Z. 현재 champion은 alias=Z로 옳게 읽히나(권위=alias), archived 도출(감사 이력 기반)은 Z를 비활성으로 오판. 결정 7-2가 막았다고 선언한 오염이 재발. 라운드 4 B-new 면2와 동일 버그-클래스.
- 왜 blocker(설계부 기준): 기법(락 종류)을 요구하는 게 아니다. 경계의 완전성(어떤 쓰기 주체가 경계 안인가)과 결정이 스스로 내건 정확성 주장("archived 오염 없음")의 정합은 설계부 몫이다. 현재 결정은 그 주장을 반증 가능한 채로 둔다.
- 제안: 결정 7에 한 줄 — (a) 부트스트랩 reconcile/seed는 콘솔이 승인/롤백 요청을 수락하기 전에 완료(startup lifespan에서 실행 후 라우팅 개시), 또는 (b) reconcile/seed도 해당 featureset의 직렬화 임계구간 획득. 둘 중 하나의 존재만 명시(기법은 명세부). (a)를 한 줄로 명문화하면 즉시 강등 가능.

> **[reviser 응답]** 해소: 결정 7-2에 **경계 완전성** 항을 신설(`decisions.md` 결정 7-2 새 불릿 + 7-1 직렬화 주체 목록 정정). reconcile·bootstrap-seed도 (alias 읽기 + 감사 쓰기)에 쓰는 제3 쓰기 주체임을 인정하고, 제안 (a)를 채택: **부트스트랩 reconcile/seed는 콘솔이 승인/롤백 요청을 수락하기 *전에* startup lifespan에서 완료하고, 그 후에야 라우팅(승인/롤백 수락)을 개시**한다. 이로써 reconcile/seed와 승인이 시간상 상호배제되어(부트스트랩=요청 수락 전) 반례의 인터리브(reconcile 뒤늦게 쓰기)가 구조적으로 불가능. "archived 오염 없음" 단언의 전제(reconcile/seed↔승인 상호배제)를 본문에 못 박아 반증 가능성을 제거. 기법(lifespan hook·라우팅 게이트)은 명세부로 명시 이관, 경계 완전성만 설계부에서 확정. 결정 7-1(승인/롤백 직렬화)·7-2(alias 권위)와 모순 없음 — 부트스트랩은 임계구간 *바깥*이 아니라 임계구간이 열리기 *전*에 끝남.

## major
### MJ-r5. 전파 확인(2-A)의 타겟이 직렬화된 연속 승인 하에서 이동 — 2-A와 결정 7이 미조정
- 문제: 2-A는 "swap/rollback 후 /health run_id가 새 버전과 일치하는지 폴링"으로 전파 확인(decisions.md:66). 그러나 결정 7-1은 동시/연속 승인을 인정하고 직렬화(decisions.md:173). A(→V2)·B(→V3)가 임계구간을 순차 통과하면 alias·서빙은 V3로 수렴하는데, A의 확인 루프는 "새 버전=V2"를 폴링 → V2는 이미 V3에 정당히 대체됐는데 A는 V2를 영원히 "전파 대기/실패"로 표시·경보(거짓 실패). 라운드 4에서 각각 추가된 두 보완(MJ-new1 전파확인, B-new 동시성)이 서로 조정되지 않았다.
- 완화(blocker 아님): 최종 alias·감사는 정확하고 최신 승인(B)의 폴링은 진짜 미전파를 표면화하므로 안전 속성 보존 — 거짓 실패는 대체된 승인의 표시에 국한.
- 제안: 확인 타겟을 "그 swap의 버전"이 아니라 "현재 active_version(alias)"로 정의하면 A·B 모두 V3 수렴 시 확인됨으로 닫힌다. 한 줄 정정.

> **[reviser 응답]** 해소: 결정 2-A 전파 확인 (a)의 타겟을 **"새 버전"→"현재 active_version(alias)"**로 정정(`decisions.md` 결정 2-A). swap/rollback 후 폴링 기준 = 서빙 `/health` run_id가 *그 swap이 쓴 버전*이 아니라 *현재 alias가 가리키는 active_version*과 일치하는지로 변경. 연속 승인 A(→V2)·B(→V3)에서 둘 다 alias가 수렴한 V3를 타겟으로 폴링하므로, A가 V2를 거짓 "전파 대기/실패"로 표시하지 않음(대체된 승인은 alias 권위상 정당히 V3에 양보). 진짜 미전파(서빙이 alias를 못 따라옴)는 여전히 active_version 불일치로 표면화 — 안전 속성 보존. 결정 7(alias=현재 활성 권위)과 정합: 전파 확인도 alias를 진실원천으로 따름.

## minor
### mn-r5. reconcile 레코드의 prev 의미 명시 권고
- 결정 7-2 reconcile 레코드가 archived 도출에 대체된 버전을 남기려면 prev = 감사 최종 활성으로 채워야 한다(결정 4의 모든 레코드가 prev를 가진다는 스키마와 정합, decisions.md:95). 결정 7-2는 reconcile의 사유·actor만 적고 prev 의미를 명시하지 않음(decisions.md:175). archived 도출 보존을 위해 reconcile prev=감사최종활성임을 한 줄 명시 권고.

> **[reviser 응답]** 해소: 결정 7-2 reconcile 레코드 정의에 **prev = 감사 최종 활성 레코드의 버전**임을 한 줄 명시(`decisions.md` 결정 7-2). 화해가 alias 실제 상태(target)로 감사를 맞추되, prev엔 감사상 직전 최종 활성을 채워 archived 도출(이전 활성→비활성 천이)이 보존되도록 함. 결정 4의 "모든 레코드가 prev를 가진다"(decisions.md:95) 스키마와 정합.

## 판정
HOLD — blocker 1건(B-r5). 코어는 결정 7로 실제 닫혔고 기존 결정 1·2와 권위 정의 모순도 해소. 그러나 reconcile·bootstrap-seed를 직렬화 경계 밖에 남겨 결정 7-2 자신의 "archived 오염 없음" 주장을 반증 가능하게 둠. 수정은 한 줄 규모지만 경계 완전성은 설계부에서 못 박아야 하므로 blocker. blocker 0 아니므로 명세부 진입 불가.

---

# 라운드 6 — B-r5 보완 최종 흐름추적

- 대상: `design/console/decisions.md` (v4 + reviser 커밋 `6fe8bd5`)
- 검토일: 2026-06-29
- 핵심 질문: B-r5("bootstrap=요청 수락 전 완료")가 reconcile↔승인 인터리브 반례를 실제로 차단하는가. 런타임 중 reconcile 재발 경로가 있는가. MJ-r5·mn-r5 정정이 기존 결정과 모순 없이 끼워졌는가.
- 판정: **PASS — blocker 0건.** 루프 종결, 구현 명세부 진입 가능.

## PASS

### B-r5 닫힘 확인 — 부트스트랩 선완료가 반례 인터리브를 구조적으로 차단
- 결정 7-1 경계 완전성 항(`decisions.md:174`)이 reconcile·bootstrap-seed를 "(alias 읽기 + 감사 쓰기) 동일 권위쌍 주체"로 명시 귀속, 제안(a) 채택 — reconcile/seed를 startup lifespan에서 완료한 *후에야* 라우팅 개시. 반례 성립 전제(reconcile↔승인 동시 실행)가 라우팅 개시 전 단계에서 무너짐.
- 결정 7-2(`decisions.md:176`)가 "archived 오염 없음" 단언의 전제(상호배제)를 본문에 명시 귀속 → 라운드 5의 "반증 가능한 채로 둠" 제거.

### 런타임 reconcile 재발 경로 없음 (흐름 끝까지 추적)
- 수동 reconcile 엔드포인트: DDD에 정의 없음. action=reconcile은 콘솔 부트스트랩 1경로뿐(`decisions.md:176`).
- 전파 확인 실패 재시도(2-A): 재시도 대상은 서빙 reload 트리거이지 alias↔감사 reconcile이 아니다. 재시도 루프는 `/health` 폴링·표시만 하고 감사에 쓰지 않음 → 권위쌍 read-modify-write 아님, B-r5 버그-클래스 비해당.
- ②후③전 크래시 후 복구: 다음 기동의 bootstrap reconcile은 항상 라우팅 *전*에 실행 → 모든 reconcile이 startup에 묶이고 런타임에 떠다니지 않음.
- 코드 확인: 현재 코드 어디에도 런타임 auto-reconcile 트리거 없음(`deploy.py`/`app.py`).

### MJ-r5 정정 정합 — 전파 확인 타겟 = 현재 alias
- 2-A(`decisions.md:66`) 폴링 타겟을 "그 swap이 쓴 버전"→"현재 active_version(alias)"로 정정. 연속 승인 A(→V2)·B(→V3) 직렬 통과 시 둘 다 V3 수렴을 타겟으로 폴링 → A의 거짓 실패 제거, 진짜 미전파는 active_version 불일치로 표면화. 결정 7-2·결정 1(champion=active_version)과 모순 없음.

### mn-r5 정정 정합 — reconcile prev=감사최종활성
- 결정 7-2(`decisions.md:176`) reconcile 레코드 prev=감사상 최종 활성 버전, target=실제 alias 버전. 결정 4 스키마(모든 레코드 prev 보유, `decisions.md:95`)·archived 도출과 정합.

### 코드 정합·누수 무영향 (재확인)
- lockless readlink(`deploy.py:46-48`), swap prev 반환(`deploy.py:63-65`), rollback prev 미반환→콘솔 사전읽기(`deploy.py:68-70`) 모두 실코드 일치.
- v4 변경분은 운영 제어 흐름 한정. 환자단위 분할·train-only stats·0-fill 금지·mask OFF 불변.

## minor (단독으로 막지 않음 — 명세부/로드맵 권고)
- **mn-r6.** scale-out 시 reconcile/seed의 경계 승격 미명시. "bootstrap 선완료"는 단일 프로세스 전용 보장 — 다중 replica에선 reconcile/seed도 공유락 대상에 포함해야 함을 명세부/로드맵에서 보강. (MVP 단일프로세스에선 구조적으로 닫힘 → minor.)
- **mn-r7.** 전파 확인 상태의 콘솔 재기동 지속성. 전파 상태는 `/health` vs 현재 alias 폴링 파생(영속 아님)이므로 재기동 후 즉석 재계산 가능 — 명세부에서 "전파 상태=폴링 파생" 한 줄 명시 권고.

## 판정
**PASS — blocker 0건.** B-r5는 결정 7-1 경계 완전성 항(bootstrap 선완료=요청 수락 전 상호배제)으로 구조적으로 닫혔고, 흐름추적 결과 MVP 범위에 런타임 reconcile 재발 경로 없음을 확인. MJ-r5·mn-r5 정정은 결정 1·2·4·7과 모순 없이 끼워짐. 잔여 2건(mn-r6·mn-r7)은 단일프로세스 MVP를 막지 않는 minor. **루프 종결 — 구현 명세부 진입 가능.**
