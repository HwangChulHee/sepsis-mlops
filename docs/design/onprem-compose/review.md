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
