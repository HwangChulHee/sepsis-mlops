# review.md — On-Prem Compose 통합 DDD (가) 레드팀 검토

- **대상**: `docs/design/onprem-compose/decisions.md` (v2 → 검토 대상)
- **핵심 질문**: PVC→bind 번역이 불변식(예측·번들 원자성·감사 append-only·무중단 핫스왑)을 보존하는가 / `[확인됨]` 태그가 실물과 맞는가 / 결정 9 자원 제한 논리에 틈이 없는가

---

## 라운드 1 (redteam)

- **대상 commit**: `bfeca7d` (main, clean) · **검토일**: 2026-07-02
- **판정**: **HOLD — blocker 1건**

### PASS (근거와 함께)

- **전파 사슬 보존** — console approve → `deploy.swap`→`set_active`→`set_alias`가 심링크를 씀(`deploy.py:93-94,106`)은 artifacts **RW** 마운트에서, serving `admin_reload`→`_resolve_alias`가 심링크를 **재해석**(`app.py:141-147`)은 artifacts **RO** 마운트에서. RO 마운트는 갱신된 심링크 *읽기*를 막지 않으므로 bind mount 공유로 전파가 온전. 결정 2·3 성립.
- **무중단 핫스왑 + readers 무락** — `_load_all`이 `new_s`/`new_ds` 조립 후 단일 이름 리바인드(`app.py:67-68`)를 `_LOCK` 아래 수행, 리더는 `state()`에서 락 없이 `_S` 읽음(`app.py:71-74`) → GIL 하 원자 리바인드. 불변식 코드 근거 확인.
- **감사 append-only** — `audit.py:37-48` before_flush + do_orm_execute 이중 차단, 컨테이너화와 무관하게 불변. 결정 5 성립.
- **ARTIFACTS 단일 출처** — `app.py:40`과 `deploy.py:30`이 동일 `ARTIFACTS_DIR` env/기본값. 결정 2 정합.
- **SERVE_URL 배선 구멍** — `service.py:167,175` 두 곳뿐이 `http://localhost:8000` 기본값. 결정 3 정확.
- **CONSOLE_FEATURESETS 기본 단일** — `console/config.py:10-12` 기본 `["vitals"]`. 결정 8 정합 목표는 기본값에서 이미 충족.
- **torch 버전 고정** — `deploy/Dockerfile:12` `torch==2.12.1` == `uv.lock:2727-2728`. 재현성 정확.
- **export가 vitals alias 생성** — `h4s_export_bundle.py:68-69` `set_alias(..., out.name)` + docstring. 결정 2 seed 확인.
- **console-api graceful 빈 상태** — `_reconcile_or_seed`는 `alias_target=None & last=None`이면 세 분기 어느 것도 안 탐(`service.py:135-151`). 결정 7 정확.

### blocker (1건)

**B-1 / 결정 7 depends_on 순서가 의존하는 healthcheck가 base 이미지에서 실행 불가 — 미식별 의존**

- **문제**: 결정 7은 `depends_on` + healthcheck(`/health`)로 기동 순서를 보장한다고 명시. Compose healthcheck는 K8s `httpGet`(kubelet이 컨테이너 밖에서 수행)과 달리 **컨테이너 안에서 명령 실행**. serving/console-api base 이미지는 `python:3.12-slim`으로 **curl·wget이 없다** → `test: ["CMD","curl",...]`는 항상 실패 → serving 영영 `unhealthy` → `depends_on: service_healthy` 절대 미충족 → console-api·front-nginx **기동 불가**. "K8s httpGet은 in-container HTTP 클라이언트 불요, compose healthcheck는 필요"라는 번역 함정이 결정 7에 미식별.
- **근거**: `deploy/Dockerfile:4` `FROM python:3.12-slim` ; 결정 7 라인 112 ; `deployment.yaml:90-96` `httpGet`.
- **제안**: 결정 7에 "healthcheck는 이미지에 존재하는 도구로 — slim엔 curl/wget 없으므로 `python -c 'import urllib.request,sys; ...'` 방식" 명문화. front-nginx는 alpine=busybox wget 존재라 별개.
- **[reviser 응답]** 해소. `deploy/Dockerfile`을 직접 확인 — `:4 FROM python:3.12-slim`, `:12-15` pip 설치 목록에 torch/fastapi/uvicorn/numpy/pandas/scipy/pydantic만 있고 curl/wget 없음(확인). 결정 7에 신규 **★bullet "healthcheck는 이미지에 존재하는 도구로만 구현 (B-1)"** 추가 — `test: ["CMD","python","-c","import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"]` 방식을 의존으로 명문화하고, front-nginx는 alpine busybox `wget` 존재라 별개(`wget -qO-`)임을 명시. curl 추가설치 대안은 슬림 유지 원칙과 어긋나 기각. 결정 7 검토 요청 항목·결정 2 검토 항목에도 "healthcheck가 이미지 내장 도구로 짜였는지" 반영.

### major

- **M-1 / 결정 9 `deploy.resources` 무시 주장의 `[확인됨]`이 현행 `docker compose` v2에서 부정확** — 레거시 `docker-compose`(v1)에선 참이나 현행 `docker compose`(v2, Go)는 `deploy.resources.limits.cpus/memory`를 `up`에서 **적용**. 채택 구현(최상위 `cpus:`/`mem_limit:`)은 v2에서도 유효 → 결과물은 안 깨짐(blocker 아님). 근거를 "v1에선 무시 / v2에서도 최상위 키는 지원되므로 이를 쓴다"로 정정 필요.
- **[reviser 응답]** 해소. 결정 9 **결정 문장**을 "`deploy.resources.limits`에 의존하지 않는다 — 최상위 키는 v1·v2 양쪽에서 up에 적용되어 이식성이 가장 넓다"로 교체. **근거 bullet**의 `[확인됨: … 비-Swarm 실행 시 미적용]`을 삭제하고 "v1은 무시/v2는 적용 [검증 필요: 설치 버전 실적용 대조 — 구현검증 2]"로 버전 명시 정정. 고려한 대안 (i)·서비스 맵 각주도 "무시됨"→"v1·v2 양쪽 적용" 표현으로 동기화.
- **M-2 / console-api를 serving `service_healthy`에 묶으면 가용성 부당 결합** — console-api 부팅은 serving 미호출(호출은 승인/롤백 시 `_propagate_and_confirm`뿐, `service.py:157-161,183-184`). `service_healthy`로 걸면 (a) UI가 ~300s 캘리브레이션 동안 불가용, (b) seed 누락으로 serving stuck-unhealthy면 console-api 영영 기동 불가 → "graceful 빈 상태 표시" 안전성 무력화. 의존 제거 또는 `condition: service_started`로 완화.
- **[reviser 응답]** 해소. `service.py` 직접 확인 — `lifespan`(`:157-161`)은 `_reconcile_or_seed(fs)`만 순회, 그 내부(`:135-150`)는 `deploy.active_version`·`audit`만 접근하고 serving HTTP 미호출; serving 호출은 `_propagate_and_confirm`(`:183-184`)→`_trigger_reload`(`:167`)·`_get_health`(`:175`)로 승인/롤백 시에만(확인). 결정 7 **결정 문장**에 "console-api의 serving 의존은 `service_healthy`가 아니라 `service_started`로 완화" 추가 + 근거 섹션에 신규 **★bullet "console-api의 serving 부팅 의존 완화 (M-2)"**로 (a)UI 300s 불가용·(b)stuck-unhealthy 시 진단 불가 논거 명문화. 결정 7 검토 요청 항목의 "console-api가 serving 없이도 부팅"에 `service_started` 명시.
- **M-3 / bind mount uid 쓰기 권한이 임계 쓰기 경로(심링크 스왑·감사 append)에 놓임** — K8s는 `fsGroup:10001`(console-api.yaml:37)로 보장하나 Compose bind mount엔 fsGroup 없음. "최소=비-root" 유지 시 호스트 소유권 미정합이면 `deploy.py:106 set_alias`·`audit.py:56 create_all`이 permission denied. 부가가 아니라 필수 경로 → major 격상. auditdb는 named volume 회피 가능, artifacts는 호스트 소유권/`user:` 정합 필수.
- **[reviser 응답]** 해소. 실물 확인 — `console-api.yaml:33,37` `fsGroup: 10001`·`:53 runAsUser: 10001`(K8s 보장), 심링크 스왑은 `serve/bundle.py:34-39 set_alias`(tmp 심링크→`os.replace`, `retrain/deploy.py`가 호출; 원 지적의 `deploy.py:106`은 위치만 상이·실체 동일), 감사 쓰기는 `audit.py:56 create_all`·`:59-62 append`(확인). 결정 2 **미결/옵션을 삭제하고 신규 ★필수 항목 "bind mount 쓰기 권한 정합 (M-3)"**로 격상 — auditdb=named volume 회피, artifacts=호스트 소유권+컨테이너 `user:` 정합 필수를 핸드오프 확정 항목으로 명문화. 결정 6 미결에도 "비-root 유지 시 M-3가 필수 선결" 연결.
- **M-4 / 알려진 한계의 xgboost `[확인됨]` 태그가 실물과 어긋남** — 스택은 GRU-only(xgboost 미설치)이고 인용한 Dockerfile 주석은 torch 고정에 관한 것. mislabel. 삭제 또는 "torch/numpy 버전 고정"으로 교체.
- **[reviser 응답]** 해소. `deploy/Dockerfile:3` 주석 "no MLflow/pandas/xgboost in the image" + pip 목록에 xgboost 없음 확인(GRU-only). 알려진 한계의 "best_iter/수치 재현 = xgboost 3.3.0" 줄을 **삭제하고 "수치 재현 = torch/numpy 버전 고정 전제"**로 교체(`Dockerfile:9-15` torch==2.12.1·numpy==2.5.0 고정 주석 인용), xgboost는 별도 벤치 하니스 소관임을 명시.

### minor

- **결정 8 전제 부정확**: "이 목록에 `vitals_labs`가 포함돼 있는데"는 기본값 `["vitals"]`와 어긋남 — 유령 featureset은 env 오버라이드 시에만. 서술 정정 권장.
- **seed 명령 모호성**: 인자 없이 실행 시 기본 `["vitals","vitals_labs"]` → vitals_labs 런 없으면 `RuntimeError` 비-0 종료로 운영자 오인. handoff에 `... h4s_export_bundle vitals` 단일 인자 명시 권장.
- **"크래시루프" 용어 부정확**: 실제론 uvicorn 살아있고 `/health` 500 반복 stuck-unhealthy. precondition explicit-exit + restart 결합 시에야 진짜 crash-loop. 용어 정정.
- **grafana/prometheus 무인증 노출 미기재**: 서비스 맵이 grafana:3000·prometheus:9090 호스트 공개하나 한계는 "무인증 콘솔"만. 동일 등급 노출 기재 권장.

- **[reviser 응답] (minor 4건 일괄)**
  1. 결정 8 B-R0-4 서술을 "기본값은 이미 `["vitals"]` 단일이라 기본 상태에선 정합, 유령 featureset은 `CONSOLE_FEATURESETS`를 env로 `vitals_labs`까지 오버라이드했을 때에만 발생"으로 정정(`console/config.py:10-12` 기본 `["vitals"]` 확인).
  2. 결정 2 결정 문장을 `h4s_export_bundle vitals` **단일 인자**로 명시 + "인자 없으면 기본 순회가 vitals_labs까지 돌다 RuntimeError 비-0 종료" 경고 추가. 결정 7 precondition 에러 메시지도 `vitals` 인자 포함으로 갱신.
  3. "크래시루프"→**stuck-unhealthy**(uvicorn 생존·`/health` 500 반복) 용어 정정을 결정 7 B-R0-3·결정 2 검토항목·상태줄·개정이력에 반영. 진짜 crash-loop은 precondition explicit-exit + `restart` 결합 시에만임을 결정 7 미결에 명시.
  4. 알려진 한계에 **"무인증 관측 대시보드 — grafana(3000)·prometheus(9090) 무인증 호스트 공개"**를 콘솔과 동일 등급 노출로 신규 기재.

## 구현 검증 항목 (런타임 실측 — 설계 blocker 아님)

1. WSL2 bind mount 상대 심링크가 컨테이너 안에서 version dir로 해석되는지(결정 2, 라인 52).
2. 설치된 `docker compose` 버전에서 최상위 `cpus:`·`mem_limit:` cgroup 실적용 + `deploy.resources.limits` v2 적용 여부 대조(결정 9, M-1).
3. 빈 artifacts로 `/health` 첫 호출 시 `_load_all` 예외 경로·메시지, precondition이 "읽을 수 있는 실패"로 바꾸는지(결정 7, 라인 118·185).
4. 핫스왑 순간 RSS 2배가 `mem_limit`(권장 1Gi) 안에서 OOM 없이 수용되는지(결정 9, 라인 145·149).
5. export가 만든 파일/폴더 소유권과 컨테이너 uid(10001) 정합(결정 2 미결, M-3 연계).
6. 첫 `/health`가 300-trial 동기 수행 → healthcheck `timeout`·`start_period`가 최악 부팅 커버하는지(결정 7·9).

---

**blocker: 1건** (B-1). blocker≠0 → **HOLD**. major 4·minor 4는 단독 통과 저지는 아니나 handoff 전 반영 강력 권장(특히 M-2·M-3).

---

## 라운드 2 (redteam)

- **대상 commit**: v3 (R1 보완 반영본) · **검토일**: 2026-07-02
- **핵심 질문**: R1 보완(B-1·M-1~M-4)이 진짜 해소됐나 / 보완이 새 결함을 만들었나
- **판정**: **HOLD — blocker 2건** (B2-1, B2-2)

### PASS (R1 보완 검증 — 실물 대조)

- **B-1 serving 부분 정확** — `deploy/Dockerfile:4 FROM python:3.12-slim`(curl/wget 미설치). serving `/health` 실재(`app.py:110-115`). `python -c urllib.request` 명령은 serving에서 유효. stuck 검토: `/health`→`_load_all` 실패 시 500→`urlopen`이 `HTTPError` **raise**→미포착→비-0 종료→**올바로** unhealthy. false-healthy 경로 없음.
- **M-2 정확** — lifespan(`service.py:157-161`)은 `_reconcile_or_seed`만 순회, serving HTTP 미호출. `service_started` 완화 정당, 새 레이스 없음(미-seed면 lifespan no-op graceful, seed는 up 전 수동).
- **M-3 인용 정정 정확** — `bundle.py:25-39 set_alias`(`os.replace:39`) 실재, `audit.py:56 create_all` 실재, `console-api.yaml:37 fsGroup:10001` 실재. (단 **해결 방식**은 B2-2 참조 — 인용은 맞으나 처방이 틀림.)
- **M-4 정확** — `Dockerfile:12 torch==2.12.1`·`:15 numpy==2.5.0` 고정, xgboost 미설치. GRU-only 정당.
- **M-1 정정 적절** — `deploy.resources` 무시 `[확인됨]` 삭제→버전 명시+구현검증 이관. 과대주장 제거.

### blocker (2건)

**B2-1 / 결정 7 B-1 보완이 console-api에 대해 틀렸다 — 기동 백본 재붕괴 (base 이미지·healthcheck 엔드포인트 이중 오류)**

- **문제**: B-1 bullet(결정 7, decisions.md:120)은 serving과 console-api를 한데 묶어 `[확인됨]`으로 "둘 다 `python:3.12-slim`, healthcheck는 `urlopen('.../health')`"라 명문화했으나 실물은 둘 다 틀림:
  1. **base가 slim이 아니라 alpine** — console-api는 `python:3.12-alpine`(`Dockerfile.api:14`). alpine은 busybox wget 보유 → "curl/wget 부재" 전제가 console-api엔 거짓.
  2. **`/health` 라우트 부재** — `console/api.py`는 `/console/versions`·`/console/versions/{v}`·`/console/audit`·`/console/approve`·`/console/rollback` 5개뿐, `/health` 없음. K8s도 프로브를 `/console/versions?fs=vitals`로 검(`console-api.yaml:81,87,95`). B-1이 처방한 `urlopen('.../health')`를 console-api에 쓰면 404→HTTPError→비-0→영구 unhealthy → B-1과 **같은 부류 결함이 console-api에 재발**.
- **부수 내부모순**: 결정 7 체인(:119)은 console-api를 healthcheck 불요(`service_started`)로 취급하는데 B-1 bullet(:120)은 healthcheck를 부여 — 문서 내 불일치.
- **근거**: `Dockerfile.api:14`(alpine) · `console/api.py:44,49,54,77,87`(/health 부재) · `console-api.yaml:81,87,95`(프로브=/console/versions) · decisions.md:119-120.
- **제안**: (a) console-api healthcheck 엔드포인트를 `/console/versions?fs=vitals`(빈 상태에서도 200)로, (b) base `[확인됨]`을 serving=slim/console-api=alpine 분리 정정, (c) console-api가 healthcheck를 갖는지/`service_started`만인지 체인과 정합.
- **[reviser 응답]** 해소. 실물 직접 확인 — `Dockerfile.api:14 FROM python:3.12-alpine`(slim 아님), `api.py`에 `/health` 부재(라우트 5개: `/console/versions`·`/console/versions/{v}`·`/console/audit`·`/console/approve`·`/console/rollback`, `:44,49,54,77,87`), `console-api.yaml:81,87,95` 프로브=`/console/versions?fs=vitals`(`:87` 주석 "빈 클러스터 200 확인됨"). 결정 7의 R1 B-1 bullet을 **서비스별로 분리 정정**(decisions.md 결정 7) — serving=slim+python urllib+`/health`, console-api=alpine+busybox wget(또는 urllib)+`/console/versions?fs=vitals`, front-nginx=alpine+wget+`/`. base `[확인됨]` 태그를 serving=slim/console-api=alpine으로 분리. **내부모순 해소**: console-api는 실제 동작하는 healthcheck를 가지며, **front-nginx→console-api는 `service_healthy`로 확정**(빈 상태 200이라 값싸고 502 창 제거), console-api→serving 방향은 M-2대로 `service_started`(방향이 다름을 결정 문장·근거 섹션·검토요청 항목에 명시). 개정이력 v4·검토요청 항목에도 반영.

**B2-2 / 결정 2 M-3의 "auditdb=named volume이면 소유권 자동 정합" 처방이 Docker 동작과 어긋남 — console-api 부팅 실패(감사 append-only 불변식 붕괴)**

- **문제**: M-3(decisions.md:58)은 "auditdb는 named volume으로 소유권 회피"라 확정하나 Docker named volume은 런타임 uid로 chown하지 않음 — 마운트 지점이 이미지에 있으면 그 소유권 복사, 없으면 **root:root 생성**. `Dockerfile.api`는 `/app/deploy/artifacts`만 mkdir(`:25`), **`/app/auditdb` 미생성** → named volume이 root 소유 → uid 10001이 db 생성 불가.
- **연쇄 붕괴**: `AuditStore`가 **모듈 임포트 시점** 인스턴스화(`service.py:30`)→생성자가 `create_all` 즉시 실행(`audit.py:54-56`)→`api.py:13`이 service 임포트 순간 root 소유 디렉토리에 sqlite 생성 시도→`OperationalError`→console-api가 **부팅 전 임포트에서 크래시**. K8s는 fsGroup:10001이 PVC 재귀 chown으로 막았으나 Compose엔 등가물 없음 — M-3이 지적한 공백을 그 처방이 못 메움. 감사 테이블 생성조차 실패.
- **근거**: `Dockerfile.api:25`(auditdb dir 미생성) · `service.py:30`(모듈레벨 AuditStore) · `audit.py:54-56`(생성자 create_all) · `console-api.yaml:37`(K8s fsGroup 해결) · decisions.md:58.
- **제안**: named volume 단독 불충분 인정 → (i) 이미지에서 `/app/auditdb`를 `chown 10001` 선생성, (ii) 엔트리포인트 init chown, (iii) root 실행(결정 6 비-root와 충돌 → 재서술) 중 택일. artifacts(bind)에 요구한 "호스트 소유권/`user:` 정합" 수준의 구체 처방이 auditdb에도 필요.
- **[reviser 응답]** 해소. 실물 직접 확인 — `Dockerfile.api:25`는 `/app/deploy/artifacts`만 mkdir(`/app/auditdb` 미생성), `service.py:30` `audit: AuditStore = AuditStore(...)` 모듈 전역, `audit.py:54-56` 생성자가 `create_all` 즉시 실행, `api.py:13` `from sepsis.console import service` 임포트가 그 트리거. 결정 2 M-3 항목에서 **"auditdb=named volume이면 자동 회피" 문구를 삭제**하고 처방을 (i)로 확정 — **콘솔 이미지 Dockerfile에서 `/app/auditdb`를 `RUN mkdir -p /app/auditdb && chown 10001:10001 /app/auditdb`로 선생성**(named volume이 이미지 소유권 uid 10001을 복사하도록). 대안 (ii)는 비-root 실행과 충돌, (iii)은 결정 6 위배라 기각 근거 명시. 부팅 전 임포트 크래시(OperationalError)와 감사 append-only 불변식 붕괴 경로를 결정 2에 명문화. 결정 5·6·검토요청·개정이력 v4에도 연결.

### major

- **M2-1 / front-nginx "alpine nginx `[확인됨]`"이 미확정 이미지 mislabel** — 결정 7(:120)이 인용한 `console-web/Dockerfile:14`는 console-web 확인일 뿐 front-nginx가 아님(front-nginx는 신규, base 미결정). handoff가 debian nginx를 고르면 busybox wget 부재로 B-1과 동종 결함(단 front-nginx healthcheck는 비-게이트라 파급 없음). `[확인됨]`→`[우리 결정]` 격하 + front-nginx base(alpine) 명시.
- **[reviser 응답]** 해소. `console-web/Dockerfile:14 nginxinc/nginx-unprivileged:alpine`이 console-web 확인일 뿐임을 확인. 결정 7 healthcheck bullet의 front-nginx 항목을 `[확인됨]`→**`[우리 결정]`으로 격하**하고 **front-nginx base = alpine nginx로 명시 결정**(busybox wget 가용, `test: wget -qO- http://localhost:80/`). front-nginx healthcheck가 기동 게이트 뒤끝의 **비-게이트**라 붕괴 파급이 없음(serving/console-api 게이트만 백본)도 명문화.
- **M2-2 / precondition "즉시 종료"는 lazy-load serving에 boot 훅이 없어 자동 성립 안 함** — 결정 7 B-R0-3의 "부팅 초입 alias 확인→즉시 종료"는 `app.py`에 startup/lifespan 훅이 **없고** 로드가 첫 `/health`에서 lazy 트리거(`app.py:71-74,110-115`, lifespan 미지정)이므로 성립하려면 app.py에 lifespan **신규 추가** 필요("재사용" 범위 넘는 serving 코드 변경). 이 의존이 "메시지 구현은 핸드오프"로만 처리돼 식별 불충분 → 결정 7에 boot 훅 추가를 의존으로 명시. 없으면 seed 누락은 여전히 stuck-unhealthy.
- **[reviser 응답]** 해소. 실물 확인 — `app.py:31 FastAPI(...)` lifespan 미지정, 로드는 `state()`가 `"pred" not in _S`일 때 `_load_all` lazy 트리거(`:72-73`), `/health`(`:110-115`)가 state() 호출. 결정 7 B-R0-3에 **★서브항목 "boot 훅이 없어 자동 성립 안 함 (M2-2)"** 신규 추가 — "부팅 초입 즉시 종료"를 실현하려면 **app.py에 lifespan(startup) 훅을 신규 추가**해 alias 유무를 부팅 시 확인·없으면 즉시 비-0 종료해야 하며, 이는 "serving 이미지 재사용" 범위를 넘는 serving 코드 변경임을 정직히 의존으로 기록. 훅 미추가 시 seed 누락이 여전히 stuck-unhealthy로 남음도 명시. 검토요청 항목·개정이력 v4 반영.

### minor

- **결정 5 auditdb 볼륨 선택이 결정 2 M-3과 미연결** — 결정 5 검토요청(:100)이 여전히 "bind vs named"를 열어둠(M-3은 named 확정). 결정 5가 M-3 참조하도록 정리 + B2-2로 named 단독 불충분 반영.
- **`[확인됨]` 인용문 "no pandas"가 실물과 어긋남** — 알려진 한계(:189)가 `Dockerfile:3` "no MLflow/pandas/xgboost"를 인용하나 `:15`가 `pandas==2.3.3` **설치**(drift용). 실질 주장(no xgboost)은 맞으나 pandas 부분 부정확 → 인용 손질.
- **front-nginx→web/api `service_started` 과도기 502 미기재** — upstream 미준비 동안 502 창을 한계로 명시.
- **grafana/renderer 기동·볼륨·depends_on 미재기술** — 결정 4는 prometheus 타깃만. grafana←prometheus, renderer←grafana depends·provisioning 볼륨 승계를 최소 한 줄 확인.

- **[reviser 응답] (minor 4건 일괄)**
  1. **결정 5 auditdb 볼륨 선택**: 검토요청(:100)의 "bind vs named 열어둠"을 종결 — 결정 5에 **★"소유권 처방은 결정 2(M-3/B2-2)에서 확정"** 추가, named든 bind든 소유권이 자동 정합 안 되며(named도 마운트 지점 미생성 시 root:root) 결정 2의 `mkdir + chown 10001` 선생성 처방을 따름을 명시.
  2. **"no pandas" 인용 손질**: 알려진 한계에서 `Dockerfile:3` 주석 "no MLflow/pandas/xgboost"가 `:15`의 `pandas==2.3.3` 설치와 어긋남을 확인(직접 확인). 실질 주장을 "no xgboost/mlflow, GRU-only"로 정정하고 pandas는 drift용으로 실제 설치됨을 괄호주로 명시(주석 인용을 pandas 부분에서 신뢰 말 것).
  3. **front-nginx 과도기 502**: 알려진 한계에 신규 항목 추가 — `service_healthy`로 기동 초기 502는 대체로 제거했으나 런타임 upstream 재시작·일시 unhealthy 동안 짧은 502 창이 가능함을 POC 한계로 명시(재시도 프록시 튜닝은 백로그).
  4. **grafana/renderer 기동·볼륨**: 실물 확인 — `deploy/monitoring/docker-compose.yml:25 depends_on: [prometheus, renderer]`(grafana가 둘 다 의존), renderer 무의존, `:34-36` provisioning·dashboards 볼륨(RO). 서비스 맵에 신규 한 줄 **"모니터링 서브스택 기동·볼륨 승계"**로 depends 구조·프로비저닝 볼륨 승계·prometheus 타깃만 재배선을 명문화.

### 구현 검증 항목 (라운드 2 추가 — 라운드 1 목록과 병합)

- (신규) console-api alpine 이미지에서 `/console/versions?fs=vitals` healthcheck가 빈 상태에서 200 반환하는지 실측.
- (신규) `/app/auditdb` chown 처방 적용 후 uid 10001로 sqlite create_all 성공하는지 실측.

---

**blocker: 2건** (B2-1, B2-2). blocker≠0 → **HOLD**. 둘 다 R1 보완이 새로 만든/못 본 결함 — B2-1은 B-1 처방이 console-api 실물(alpine·/health 부재)을 틀렸고, B2-2는 M-3 named-volume 처방이 Docker 소유권 동작과 어긋나 감사 부팅을 깬다. major 2·minor 4는 handoff 전 반영 권장.
