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

---

## 라운드 3 (redteam)

- **대상 commit**: v4 (R2 보완 반영본) · **검토일**: 2026-07-02
- **핵심 질문**: R2 보완(B2-1·B2-2·M2-1·M2-2)이 진짜 해소됐나(가짜 수렴) / v4가 결정 7에 쌓은 healthcheck·의존·lifespan 사이 새 내부모순이 생겼나
- **판정**: **HOLD — blocker 1건** (B3-1)

### PASS (R2 보완 검증 — 실물 대조, 가짜 수렴 아님)

- **B2-1 console-api 엔드포인트 빈 상태 200 확정** — `/console/versions?fs=vitals`→`list_versions("vitals")` 코드 추적: `deploy.active_version` 심링크 없으면 `None` 무예외(`deploy.py:88-90`), audit 빈 테이블 SELECT→`[]`(`service.py:206-208`, `audit.py:87-108`), `_scan_version_ids` 빈 iterdir→`[]`(`service.py:199-203`). 반환 `{"featureset":"vitals","active":None,"versions":[]}`→**200**(`api.py:44-46`). 5xx 경로 없음. base=alpine·`/health` 부재도 확인. 처방 정확.
- **B2-1 healthcheck-부팅 경합 무혐의** — `list_versions`는 순수 read, lifespan `_reconcile_or_seed`는 `yield` 전 완료(`service.py:157-161`)라 uvicorn 라우팅 열기 전 → healthcheck HTTP 도달 불가. 레이스 없음.
- **B2-2 chown 처방 Docker 동작 정합** — `Dockerfile.api:25`가 artifacts만 mkdir(auditdb 미생성), `AuditStore` 모듈전역(`service.py:30`)·생성자 `create_all` 즉시(`audit.py:54-56`)·`api.py:13` 임포트 트리거 = 부팅 전 크래시 경로 실재. `RUN mkdir -p /app/auditdb && chown 10001:10001`은 "빈 named volume 최초 마운트 시 이미지 마운트지점 소유권 복사" 동작과 일치(canonical). 정확.
- **M2-1 정확** — `console-web/Dockerfile:14`는 console-web 확인일 뿐(front-nginx 아님). `[우리 결정]` 격하+base=alpine 명시 정당.
- **M2-2 정확** — `app.py:31 FastAPI(...)` lifespan 미지정, 로드는 `state()` lazy(`app.py:72-73`), `/health`가 state() 호출. boot precondition 즉시종료엔 app.py lifespan 신규 추가 필요=serving 코드 변경, 정직히 기록됨. (console-api는 lifespan 있음 `api.py:15` — 서비스별 올바르게 구분.)

### blocker (1건)

**B3-1 / 결정 7이 front-nginx를 console-web `service_healthy`에 게이트하나 console-web healthcheck를 어디에도 명세 안 함 — 게이트 미충족으로 콘솔 UI 기동 불가 (B-1·B2-1과 동종 함정, 4번째 서비스로 이동)**

- **문제**: 결정 7(`decisions.md:128`)·알려진 한계(`:204`)는 "front-nginx는 web+api healthy 뒤(`service_healthy`)"라 명시하나, 결정 7 healthcheck bullet은 serving·console-api·front-nginx **세 서비스만** test를 규정하고 **console-web healthcheck는 전혀 규정 안 함**. console-web 이미지에도 `HEALTHCHECK` 없음(`console-web/Dockerfile:1-22`, nginx-unprivileged:alpine). Compose에서 `depends_on: console-web: {condition: service_healthy}`인데 대상에 healthcheck 없으면 그 condition은 **영원히 미충족**→front-nginx 기동 안 함.
- **파급**: 서비스 맵상 호스트 공개는 front-nginx:80뿐이고 console-web/api는 내부 → front-nginx 미기동 시 **브라우저 콘솔 UI(핵심 산출물) 진입점 통째로 불가용**. B-1·B2-1이 3라운드 잡아온 "게이트가 의존하는 healthcheck가 실재/작동 안 해 기동 백본 붕괴" 함정의 재발(대상만 console-web으로 이동).
- **근거**: `decisions.md:128,204` · 결정 7 bullet(console-web 누락) · `console-web/Dockerfile:1-22`(HEALTHCHECK 부재).
- **제안**: 택1 — (a) console-web healthcheck를 결정 7에 명세(alpine busybox `wget -qO- http://localhost:8080/`, `/`는 SPA 폴백 try_files로 항상 200 — `console-web/Dockerfile:19`), 또는 (b) front-nginx→console-web 의존을 `service_started`로 완화(front-nginx는 자칭 비-게이트 뒤끝, web 준비 전 짧은 502 창은 이미 한계 `:204`에 기재). 현 "healthcheck 없이 service_healthy 게이트" 상태 해소 필수.
- **[reviser 응답]** 해소 — **(a) 채택**. `console-web/Dockerfile` 직접 확인: base `nginxinc/nginx-unprivileged:alpine`(`:14`), 비-root uid 101·**8080** listen(`:12,17,19,20,21` — "listen 8080 — 비-root 라 80 바인드 불가"), SPA 폴백 `try_files $uri $uri/ /index.html`(`:19` baked default.conf), **`HEALTHCHECK` 지시어 전체 부재**(`:1-22`) 확인. 결정 7 healthcheck 섹션에 **★console-web bullet(B3-1) 신규 추가** — alpine busybox `wget`으로 `test: ["CMD","wget","-q","--spider","http://localhost:8080/"]`(또는 `wget -qO- ... || exit 1`), `/`는 SPA 폴백이라 항상 200(포트는 80 아닌 8080임을 근거와 함께 명시). 이로써 front-nginx가 게이트하는 **모든** 대상(console-api·console-web)에 실재·작동 healthcheck가 갖춰져 "healthcheck 없이 service_healthy 게이트" 모순 제거. 대안 (b) `service_started` 완화는 502 창을 남겨 기각(healthy 게이트가 502 창 자체를 없앰). healthcheck 섹션 헤딩도 `B-1 / B2-1 / B3-1 정정`으로 갱신, 개정이력 v5·상태줄 반영.

### major

- **M3-1 / 결정 7 기동 체인 "(1) serving healthy 후 (2) console-api" 서술이 M-2 `service_started` 완화와 내부모순** — `decisions.md:128` 번호 체인은 console-api가 serving healthy 후 온다고 하나, M-2 bullet(`:138`)은 console-api→serving을 `service_started`로 완화 확정. 충돌 — 기계적 핸드오프가 번호 체인만 보고 `service_healthy`로 걸면 M-2가 제거한 결함(캘리브레이션 300s 콘솔 불가용·seed 누락 시 진단 불가) 부활. **제안**: 번호 체인 (1)(2)를 "console-api는 serving `service_started`만 의존(healthy 대기 안 함)"으로 정정해 M-2와 일치. (권위 결정은 M-2라 blocker 아니나 부활 위험 실재.)
- **[reviser 응답]** 해소. 결정 7 결정 문장의 **번호 순서 서술 "(1) serving healthy 후 (2) console-api …"를 폐기**하고, 기동 체인을 **방향별 condition으로 한 곳에서 재서술** — serving=무의존(맨 앞), **console-api→serving=`service_started`**(`service_healthy` 아님, M-2/M3-1 근거 명시), front-nginx→console-api=`service_healthy`, front-nginx→console-web=`service_healthy`(B3-1). "번호가 아니라 condition이 권위"이고 console-api→serving(느슨)과 front-nginx→web/api(엄격)는 **방향이 다름**을 명문화. 과거 번호 서술이 M-2와 모순이라 폐기했음도 체인 끝에 남김. 개정이력 v5 반영.

### minor

- **서비스 맵이 front-nginx base=alpine 반영 안 함** — M2-1이 alpine 확정했으나 서비스 맵(`:183`) "빌드 소스"는 "nginx conf (신규)"로 base 미표기. alpine 명시 권장.
- **기존 root 소유 auditdb named volume은 재-chown 안 됨** — B2-2 처방은 빈(최초) volume에만 적용. 이전 실행으로 root 소유 volume 있으면 재빌드해도 root 유지→크래시. POC 최초는 fresh라 무해하나 재배포 시 `docker volume rm` 선행 필요를 운영노트에 한 줄 명시 권장.

- **[reviser 응답] (minor 2건 일괄)**
  1. **서비스 맵 front-nginx base=alpine**: 서비스 맵 "빌드 소스"를 `nginx conf (신규, base=alpine — M2-1)`로 갱신(M2-1 alpine 확정 반영).
  2. **auditdb named volume 재-chown 한계**: 알려진 한계에 신규 한 줄 추가 — B2-2의 `mkdir + chown 10001` 처방은 named volume이 최초(빈) 마운트일 때만 이미지 소유권을 복사하므로, 이전 실행으로 root 소유가 된 기존 volume은 재빌드해도 root로 남아 uid 10001 create_all 실패(부팅 전 크래시). **재배포·이미지 교체 시 `docker volume rm <auditdb_volume>` 선행** 필요를 운영노트로 명시(근본 해결=엔트리포인트 chown/fsGroup 등가물은 결정 6·POC 범위상 백로그).

### 구현 검증 항목 (라운드 3 — 기존 목록과 병합)

- (신규) front-nginx→console-web `service_healthy`(또는 `service_started`) 배선 시 `docker compose up`이 console-web healthcheck 미정의로 오류/무한대기 안 하는지 실측 — B3-1 해소 후 재확인.

---

**blocker: 1건** (B3-1). blocker≠0 → **HOLD**. R2 blocker(B2-1·B2-2)·M2-1·M2-2 보완은 실물 대조 결과 **정확히 해소**(가짜 수렴 아님). B3-1은 v4에도 남아 있던 아직 못 본 층 — 결정 7이 세 healthcheck는 정밀 규정하면서 정작 `service_healthy`로 게이트하는 네 번째(console-web)의 healthcheck를 규정 안 해 동종 함정이 남음. M3-1은 M-2 완화가 번호 체인 서술과 남긴 내부모순.

---

## 라운드 4 (redteam) — 수렴 판정

- **대상 commit**: v5 (R3 보완 반영본) · **검토일**: 2026-07-02
- **핵심 질문**: healthcheck-게이트 계열(B-1→B2-1→B3-1)이 진짜 다 닫혔나(가짜 수렴) / v5가 아직 아무도 안 본 축(모니터링·불변식·자원·결정간 참조·문서 일관성)에 새 blocker를 남겼나
- **판정**: **PASS — blocker 0건**

### PASS (R3 보완 검증 — 실물 대조, 가짜 수렴 아님)

- **B3-1 console-web healthcheck 명세·정합 확정** — 결정 7이 console-web healthcheck를 `test: wget -q --spider http://localhost:8080/`로 신규 명세. 실물 `console-web/Dockerfile:14`(base alpine → busybox wget 보유), `:19`(`listen 8080` + `try_files $uri $uri/ /index.html`), `:21`(EXPOSE 8080), HEALTHCHECK 지시어 전체 부재(`:1-22`) 모두 확인. 명세 포트(8080)가 실제 listen 포트와 일치, `/`는 SPA 폴백 항상 200. 처방 정확.
- **healthcheck-게이트 계열 전수 확인 — 계열이 닫혔다** — front-nginx가 `service_healthy`로 게이트하는 대상 전수 열거: console-api·console-web **두 서비스뿐**. 둘 다 실재·작동 healthcheck 보유(console-api=`/console/versions?fs=vitals` 빈 상태 200 — `console-api.yaml:87`, console-web=`/` SPA 폴백 200). serving은 `service_started`로만 의존받음(게이트 백본 아님)에도 자체 `/health` 보유. **healthy로 게이트되는데 healthcheck 없는 서비스가 더는 없음** — B-1/B2-1/B3-1 계열 완전 종결.
- **M3-1 기동 체인 방향별 재서술 정합** — 결정 7이 번호 서술 폐기, 방향별 condition으로 재서술(serving=무의존, console-api→serving=`service_started`, front-nginx→console-api·console-web=`service_healthy`). "번호가 아니라 condition이 권위"·"방향이 다름" 명문. M-2와 모순 없음, 새 모순 없음.
- **불변식 4개 v5 보존** — 예측/추론(`app.py:82-107` NaN 계약 불변), 번들 원자성(`bundle.py`), 감사 append-only(`audit.py` 훅 + B2-2 chown이 create_all 성공 보장), 무중단 핫스왑(`app.py:62-68` 원자 리바인드). v1~v5 변경 어느 것도 훼손 안 함.
- **결정 9 자원 정렬 실재** — `deployment.yaml:127 limits.memory:1Gi`·`:126 limits.cpu:2`·`:81-84 BLAS 캡=2`·`:96 startupProbe 300s`. 결정 9·7 정렬 근거 실재.
- **모니터링 서브스택 정합** — `docker-compose.yml:25` grafana `depends_on:[prometheus,renderer]`(plain=service_started), `:34-36` provisioning 볼륨, renderer 무의존. 결정 4·서비스맵 정합.

### major

없음.

### minor

- **결정 9 mem_limit 근거의 torch 이중계상(보수적 과대추정)** — "GRU RSS ~248MB × 2 + torch 런타임"에서 248MB가 이미 torch 포함이면 `×2`가 torch 이중계상(핫스왑은 모델 가중치만 2배). **과대추정 방향이라 안전**(실피크 < 1Gi, K8s 1Gi 정렬값). 표현만 "모델 상태 2배 + torch 1회"로 다듬으면 정확. 수치 결론(1Gi)은 유효.
- **결정 4 "serving용 extra_hosts" 표현 부정확** — 제거 대상은 실물상 **prometheus** 서비스의 `extra_hosts: host.docker.internal`(`docker-compose.yml:17-18`). "prometheus의 host.docker.internal 라인 제거"가 정확. 소유 서비스 오기.
- **모니터링 서브스택 병합 시 상대경로 리베이스 미언급** — `./prometheus.yml`·`../grafana/provisioning`(`:16,35-36`) 상대경로가 단일 통합 compose 병합 시 어긋날 수 있음. 결정은 "볼륨 승계"만 언급, 경로 리베이스 무언급. 핸드오프-레벨 배관 디테일이라 설계 blocker 아니나 한 줄 명시 권장.
- **busybox wget `--spider` 지원은 구현검증** — alpine busybox wget `--spider` 전제. 문서가 대안(`wget -qO- ... || exit 1`) 병기해 리스크 흡수됨. 구현검증 편입.

### 구현 검증 항목 (라운드 4 — 기존 목록과 병합)

- (신규) console-web·console-api·front-nginx alpine busybox `wget`이 명세 플래그(`--spider`/`-qO-`) 지원하는지 실측.
- (신규) 모니터링 서브스택 단일 통합 compose 병합 시 `prometheus.yml`·grafana provisioning 상대 볼륨 경로가 올바로 마운트되고 prometheus가 `serving:8000` DNS 해석하는지 실측.

---

**blocker: 0건 — PASS.** 세 라운드 연속(B-1→B2-1→B3-1) 서비스를 옮겨 다니던 "healthy 게이트가 의존하는 healthcheck의 실재/작동" 함정 계열이 **완전히 닫혔음을 전수 확인**. R3 보완(B3-1·M3-1)은 실물 대조 결과 정확 해소(가짜 수렴 아님). 라운드 4에서 새로 연 축(모니터링·불변식 4개·결정 9 자원·결정 2↔5↔6 및 7↔9 참조·문서 일관성)에서 blocker 없음. 남은 것은 minor 4·구현검증 항목뿐. **HOLD 해제 → 다음 단계(핸드오프) 진행 가능.**
