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
