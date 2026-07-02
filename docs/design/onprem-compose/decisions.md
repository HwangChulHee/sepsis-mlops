# On-Prem Compose 통합 설계결정문서 (DDD) — 단일 호스트 Docker Compose 배포 스택 (가)

> **설계 근거**: 운영 엔지니어 포지셔닝 자산(동서메디케어/제조·의료 온프렘 배포). "실제 병원 배포는 온프렘 컨테이너 + EMR 통합이 본체고, K8s 오케스트레이터는 이 부하(시간당 수백)엔 과하다"는 판단을 실물로 구현한다. K8s 자산(`deploy/k8s/`)은 폐기가 아니라 "써보고 과하다고 판단한 증거"로 병존한다.
> **워크플로우·출처등급**: `CLAUDE.md`. 검토(`docs/design/onprem-compose/review.md`) 통과 후 핸드오프로. 출처등급: `[확인됨]`(코드/파일 실측) · `[우리 결정]`(설계 선택) · `[검증 필요]`(미확인 가정).
> **상태**: 설계부 v3 — 정식 review-loop R1 반영(blocker 1·major 4·minor 4 해소).
> **개정 이력**
> - v1: 초안 (설계부). 설계 핑퐁 결정 반영 — 스코프(가/나 분리), 공유 볼륨(bind mount), 네트워킹(front nginx + serving 직접), 모니터링 타깃, 감사DB(sqlite), 보안 이식 수준, 기동 순서, featureset 단일.
> - v2: 예열 라운드(R0) — **B-R0-1**(Compose 자원 제한 미결 + `deploy.resources`는 `docker compose up`에서 무시됨 → BLAS 캡 오정렬로 thrash 아티팩트 재발; **결정 9 신설**), **B-R0-2**(reload 2배 메모리 vs mem_limit 미결 → OOM 위험; 결정 9에 흡수), **B-R0-3**(serving은 seed 없으면 graceful 아님·크래시루프 — console-api는 graceful 확인; 결정 7 보완), **B-R0-4**(console `CONSOLE_FEATURESETS` 순회 vs serving 단일 featureset 불일치; 결정 8 보완) 반영. 무혐의 확정: console-web ConfigMap = baked conf 동일(SPA 폴백뿐), SERVE_URL이 유일 localhost 기본, console-api reconcile은 빈 상태 no-op graceful.
> - v3: 정식 review-loop R1(redteam) 반영 —
>   - **B-1**(blocker): 결정 7 healthcheck가 `python:3.12-slim`에 curl/wget 부재로 실행 불가 → 기동 백본 붕괴. 결정 7에 "healthcheck는 이미지 내장 도구(python urllib)로만 구현" 명문화, front-nginx는 alpine busybox wget 존재로 별개 명시(`deploy/Dockerfile:4,12-15` 직접 확인).
>   - **M-1**(major): 결정 9의 "`deploy.resources`는 up에서 무시됨 `[확인됨]`"을 버전 명시로 정정 — v1은 무시/v2는 적용, 최상위 `cpus`·`mem_limit`은 v1·v2 양쪽 적용이라 이를 쓴다(실적용은 구현검증). 서비스 맵 각주·고려한 대안도 동기화.
>   - **M-2**(major): console-api를 serving `service_healthy`에 묶는 가용성 부당 결합 제거 → `condition: service_started`로 완화(console-api 부팅은 serving 미호출, `service.py:157-161,183-184` 확인). seed 누락 stuck-unhealthy여도 콘솔로 진단 가능해야 함이 근거.
>   - **M-3**(major): bind mount uid 쓰기 권한을 "미결"에서 필수 확정 항목으로 격상 — fsGroup(K8s `console-api.yaml:37`) 부재로 심링크 스왑(`bundle.py`)·감사 append(`audit.py:56`)가 permission denied 가능. auditdb=named volume 회피, artifacts=호스트 소유권/`user:` 정합 필수로 결정 2·6에 명문화.
>   - **M-4**(major): 알려진 한계의 xgboost `[확인됨: torch 버전 고정 주석]` mislabel 삭제 — 스택은 GRU-only(`Dockerfile:3` "no xgboost"), "torch/numpy 버전 고정(재현성)"으로 교체.
>   - **minor**: 결정 8 유령 featureset은 env 오버라이드 시에만임을 정정(기본 `["vitals"]`), seed 명령 `vitals` 단일 인자 권장(결정 2), "크래시루프"→"stuck-unhealthy" 용어 정정(결정 2·7), grafana:3000·prometheus:9090 무인증 노출을 알려진 한계에 추가.

## 한 줄 요약

현재 **모니터링만 컨테이너 + serving은 호스트 맨몸 프로세스**인 상태를 [확인됨: `deploy/monitoring/docker-compose.yml` — serving을 `host.docker.internal:8000`으로 스크랩], **serving·console(api+web)·모니터링을 하나의 Compose 스택**으로 통합한다. 이번 산출물은 **배포 배관(가)까지** — 부하테스트(나)는 이 스택 위에 얹는 별도 문서. K8s 매니페스트(`deploy/k8s/`)의 PVC→bind volume, Ingress→front nginx, Service DNS→Compose 서비스명으로 번역하되 **예측/추론 로직·번들 원자성·감사 append-only는 불변**.

## 범위 / 범위 외

| 범위 (가 — Compose 통합 배관) | 범위 외 |
|---|---|
| serving 컨테이너화(`deploy/Dockerfile` 재사용) | 부하테스트 시나리오·매트릭스·측정 (나 — 별도 문서) |
| console-api·console-web 통합 기동 | XGB 서빙 (별도 벤치 하니스, 이 프로덕션 스택에 없음) |
| **공유 artifacts** bind mount + 번들 seed 규약 | replica ≥2 / Redis 상태 외부화 (확장 로드맵) |
| **front nginx** = Ingress 라우팅 이식 | 무중단 *코드/이미지* 배포 (앱이 막음 — Recreate) |
| **prometheus 타깃** 재배선(`serving:8000`) | 클라우드(EKS/Fargate) 실배포 (별도 트랙, 비용표는 종이) |
| 감사DB sqlite 볼륨 영속 | EMR/HL7·FHIR 통합 (실배포 본체지만 이 스택 밖) |
| 기동 순서·healthcheck·`SERVE_URL` 배선 | 전면 인증(SSO/OIDC) — M4 범위 |

> **스코프 핵심(결정 1)**: 최종 목적지는 풀 온프렘 통합 스택. 이번 (가)는 그 **하부 배관 + 부하 대상 확보**까지고, (가)는 (나)의 부분집합이라 **버리는 작업 0**으로 설계한다(결정 1 확장성 제약).

---

## 결정 1: 스코프 — 풀 스택 목적지 중 (가) 배관까지, (가)⊂(나) 확장성 보장

- **결정**: 이번 세션 산출물은 **Compose 통합 배관(가)**다. 부하테스트(나)는 이 위에 얹는 별도 설계문서로 분리한다. 단 (가)를 지을 때 (나)로 **얹기만 하면 되도록**(serving 재작성 없이 부하 드라이버·측정만 추가) 볼륨·네트워크·포트를 잡는다.
- **근거 + 출처등급**:
  - 현재 Compose는 **모니터링 전용**이고 serving은 호스트에서 돎 → 통합은 실제 번역 작업이지 단순 묶기가 아님 [확인됨: `deploy/monitoring/docker-compose.yml`이 prometheus·grafana·renderer만, 주석 "서빙은 호스트(uvicorn 127.0.0.1:8000)"].
  - K8s 안 쓰기로 한 이상 Compose가 유일 오케스트레이션 레이어 → serving만 컨테이너화하고 console을 호스트에 남기면 어정쩡, 결국 전체가 Compose에 들어와야 온전한 배포 [우리 결정].
  - 부하 경로는 console·전파 사슬을 안 건드림 → (가)의 부하 대상(serving)만 먼저 검증하고 (나)를 얹는 순서가 "검증된 코어에 살 붙이기" [우리 결정].
- **고려한 대안**: (i) (나)부터 = 공유볼륨 크럭스가 부하테스트 목표를 가림(기각). (ii) 부하테스트 최소 스택(serving만)으로 (가) 축소 = 목적지가 풀 스택이라 어차피 다시 지음(기각 — 사용자 결정).
- **검토 요청 항목**: (가) 산출물이 (나)를 얹을 때 serving 재작성을 강제하는 지점이 없는지(볼륨/포트/네트워크가 확장 가능한지).

---

## 결정 2: ★핵심 — 공유 artifacts = bind mount(`deploy/artifacts/`) + 진짜 번들 export seed

- **결정**: serving(읽기)·console-api(쓰기)가 공유하는 번들 저장소를 **호스트 bind mount** `./deploy/artifacts` → 컨테이너 `/app/deploy/artifacts`로 둔다(named volume 아님). 최초 번들은 **`h4s_export_bundle vitals` 1회 수동 실행**으로 seed한다(Compose `up` 전 선작업 — **`vitals` 단일 인자 명시**: 인자 없이 돌리면 기본 순회가 `vitals_labs`까지 돌다 데이터 부재 시 RuntimeError로 비-0 종료해 운영자를 오인시킴). K8s의 공유 PVC(RWO)를 bind mount로 번역한 것.
- **근거 + 출처등급**:
  - serving은 `ARTIFACTS`(기본 `/app/deploy/artifacts`) 아래 활성 alias `gru_<fs>`를 해석해 번들 로드 [확인됨: `src/sepsis/serve/app.py:40` `ARTIFACTS = Path(os.environ.get("ARTIFACTS_DIR", str(C.ROOT/"deploy"/"artifacts")))`, `:45-46` `_resolve_alias`]. console-api는 `deploy.ARTIFACTS` 고정경로에 alias를 씀 → **같은 경로 마운트가 전파 정합의 핵심** [확인됨: `deploy/k8s/console/console-api.yaml` 주석 B2].
  - **모델 저장소 = 이 폴더 자체**(클라우드 registry 아님). MLflow는 학습단 로컬 sqlite(`var/mlflow.db`)고 serving은 MLflow 미참조 [확인됨: `config.py:22-24` mlflow_uri=sqlite; serving Dockerfile에 mlflow 미설치 — 함수내 lazy import]. 온프렘 규제 환경엔 클라우드 registry보다 FS-권위가 맞음 [우리 결정].
  - **alias는 상대 심링크** → bind mount여도 컨테이너/호스트 양쪽에서 동일 해석, 경계 넘는 심링크 문제 없음 [확인됨: `scripts/h4/h4s_export_bundle.py` docstring "maintains an ACTIVE ALIAS … relative symlink → active version"]. 이로써 초기 우려(bind mount 심링크 전파)가 해소됨.
  - **seed 필요성**: `deploy/artifacts/`는 gitignore+dockerignore → 신선 상태에서 비어 있고, serving은 부팅 시 번들 로드 + 300-trial 캘리브레이션이 필요해 **빈 폴더면 부팅 실패** [확인됨: `.gitignore:14`, `.dockerignore:19`; `app.py` `_load_all`이 부팅 시 번들+calibrate]. seed가 **수동 선작업**이라 `up` 시점엔 폴더가 이미 채워져 있어 기동 순서 꼬임 없음 [우리 결정].
- **고려한 대안**: (i) named volume — 전파·심링크는 안전하나 호스트에서 번들 seed가 불편, 상대 심링크로 bind의 리스크가 이미 해소돼 이점 사라짐(기각). (ii) 이미지에 번들 baking — dockerignore로 제외 중이고 런타임 마운트가 덮으므로 죽은 레이어(기각, 기존 설계 유지) [확인됨: `.dockerignore:17-19` 주석]. (iii) 합성 번들 seed — 진짜 학습본이 이미 있어 불필요한 한 겹(기각 — 사용자 결정).
- **★필수(핸드오프 확정) — bind mount 쓰기 권한 정합 (M-3)**: 이건 "옵션"이 아니라 **임계 쓰기 경로를 막을 수 있는 필수 항목**이다. K8s는 `fsGroup: 10001`로 마운트 볼륨 소유 그룹을 비-root uid에 맞춰 쓰기를 보장했으나 [확인됨: `console-api.yaml:33,37` `fsGroup: 10001`, `:53` `runAsUser: 10001`], **Compose bind mount에는 fsGroup에 해당하는 장치가 없다**. 그런데 임계 쓰기가 두 곳에서 일어난다 — (a) 승인 시 심링크 스왑(alias 교체) [확인됨: `serve/bundle.py:34-39` `set_alias`가 tmp 심링크 생성→`os.replace`; console 승인 경로 `retrain/deploy.py`가 이를 호출], (b) 감사 DB 스키마 생성/append [확인됨: `audit.py:56` `Base.metadata.create_all`, `:59-62` `append` INSERT]. 비-root(uid 10001)로 실행하는데 호스트 폴더 소유권이 어긋나면 **둘 다 permission denied**로 승인·감사가 깨진다. 따라서 핸드오프에서 반드시 확정한다: **auditdb는 named volume으로 두어 소유권 문제를 회피**할 수 있고(Docker가 볼륨 소유권을 컨테이너 uid에 맞춤), **artifacts는 bind mount라 호스트 폴더 소유권 + 컨테이너 `user:` 지정을 정합**시켜야 한다(예: 호스트 `chown 10001` 또는 seed·컨테이너 uid를 일치). export가 만든 파일 소유권과 컨테이너 실행 uid 정합도 여기서 확정 [검증 필요: WSL2 bind mount uid 매핑].
- **검토 요청 항목**: 상대 심링크가 bind mount 경계를 넘어 컨테이너 안에서 실제로 해석되는지(WSL2에서 실측 필요), seed 안 된 상태로 `up` 했을 때 serving이 graceful 실패(읽을 수 있는 precondition 에러)인지 stuck-unhealthy(원인 불명 500 반복)인지 — 결정 7 B-R0-3·B-1 참조.

---

## 결정 3: 네트워킹 — front nginx(라우팅) + serving 직접 포트, `SERVE_URL` 배선 필수

- **결정**: 밖에서의 진입을 두 갈래로 나눈다.
  1. **콘솔(사람용)**: front nginx 컨테이너(포트 80 공개)가 K8s Ingress를 이식 — `/console/*` → `console-api:8000`, `/` → `console-web:8080`. 브라우저는 동일 출처만 봐 CORS 미발생.
  2. **serving(EMR/부하용)**: nginx 우회하고 호스트 포트 `8000:8000` 직접 공개. 부하 측정에 nginx 홉이 안 껴 순수 서빙 latency 유지.
  - console-web은 **자체 nginx로 정적 서빙**(포트 8080)하므로 front nginx는 라우팅만(정적 서빙 안 함).
- **근거 + 출처등급**:
  - console-web은 멀티스테이지(vite build → `nginxinc/nginx-unprivileged:alpine`)로 dist/를 8080에 정적 서빙 + SPA 폴백 내장 [확인됨: `console-web/Dockerfile` — `EXPOSE 8080`, baked default.conf `try_files … /index.html`].
  - console-web API base는 상대경로 기본(`VITE_API_BASE ?? ""`) → "동일 출처 뒤" 전제로 빌드됨 [확인됨: `console-web/src/api.ts:13` `const BASE = import.meta.env.VITE_API_BASE ?? ""`; `App.tsx` 주석 "API base = 상대경로 /console/* (Ingress 동일출처)"]. 따라서 web은 런타임 env 불필요.
  - K8s Ingress가 `/console`→api, `/`→web으로 갈랐고 "동일 출처라 CORS 없음"이 명문 [확인됨: `deploy/k8s/console/ingress.yaml`].
  - **★`SERVE_URL` 필수 배선(구멍)**: console-api가 승인 후 serving을 부를 때 `SERVE_URL` env 사용, **기본값 `http://localhost:8000`** → 컨테이너 간에는 localhost가 자기 자신이라 serving 미도달 → **`SERVE_URL=http://serving:8000` 반드시 주입** [확인됨: `src/sepsis/console/service.py:167` `os.environ.get("SERVE_URL","http://localhost:8000")+"/admin/reload"`, `:175` `/health`]. 안 하면 승인 전파가 silent 단절.
- **고려한 대안**: (i) 포트 따로 열기(web:3001, api:8001) — CORS 헤더를 콘솔에 새로 넣어야 하고 기존 "동일 출처" 설계와 어긋남(기각). (ii) front nginx가 정적도 직접 서빙 + console-web 컨테이너 제거 — 1-nginx로 단순하나 console-web 자체완결 이미지를 뜯어야 하고 K8s 2-nginx 구조와 갈라짐(기각, 충실성 우선). (iii) serving도 nginx 뒤 — 부하 측정에 프록시 홉 지연이 껴 순수 서빙 latency 오염(기각).
- **미결/옵션**: front nginx conf는 K8s Ingress처럼 rewrite 불필요(console-api 라우트가 이미 `/console/*` 접두) [확인됨: `ingress.yaml` 주석]. 인증(basic-auth)은 M4 범위 — POC는 무인증 노출을 한계로 명시.
- **검토 요청 항목**: front nginx 라우팅이 `/console/*`와 `/`를 정확히 가르는지(경로 접두 충돌 없는지), `SERVE_URL` 주입 누락 시 승인 API가 명시적 실패하는지 silent 통과인지.

---

## 결정 4: 모니터링 — prometheus 타깃 `serving:8000`(서비스명 DNS)

- **결정**: prometheus 스크랩 타깃을 `host.docker.internal:8000` → **`serving:8000`**으로 변경(Compose 내부 서비스명 DNS). serving용 `extra_hosts: host.docker.internal` 라인은 제거(무해하나 불필요). scrape_interval은 현행 2s 유지(부하 영향 무시 수준), 필요 시 부하 중 15s로.
- **근거 + 출처등급**:
  - 현재 타깃이 호스트 향함 — serving이 호스트에서 돌던 잔재 [확인됨: `deploy/monitoring/prometheus.yml` `targets: ["host.docker.internal:8000"]`].
  - serving은 `prometheus.io/scrape` 애노테이션으로 `/metrics:8000` 노출 [확인됨: `deploy/k8s/deployment.yaml` annotations, `app.py` `/metrics` 엔드포인트].
  - Compose 내부는 서비스명이 곧 호스트네임(내장 DNS) → IP 하드코딩 불요 [우리 결정].
- **고려한 대안**: prometheus를 호스트 네트워크 모드로 — 이식성·격리 저하(기각).
- **검토 요청 항목**: scrape 2s가 부하테스트 throughput 측정에 유의미한 오염을 주는지(무시 가능 예상, 실측 확인 여지).

---

## 결정 5: 감사DB — sqlite 볼륨 영속(postgres 아님)

- **결정**: 감사 DB는 **sqlite**를 별도 볼륨(`/app/auditdb`)에 얹어 영속화. `CONSOLE_AUDIT_DB_URL=sqlite:////app/auditdb/console_audit.db` 주입. postgres 서비스 불필요.
- **근거 + 출처등급**:
  - 감사 저장은 sqlite + env 주입 구조 [확인됨: `src/sepsis/console/audit.py:55` `create_engine(url or C.audit_uri())`; `service.py:30` `AuditStore(os.environ.get("CONSOLE_AUDIT_DB_URL") or C.audit_uri())`; `config.py:28-30` dev 기본 = `var/console_audit.db`; K8s `console-api.yaml:67` `CONSOLE_AUDIT_DB_URL=sqlite:////app/auditdb/console_audit.db`].
  - 감사는 append-only(재시작/재배포에도 생존해야 규제·사고분석 성립) → 별도 영속 볼륨 [확인됨: `artifacts-pvc.yaml` 감사 PVC 주석].
  - 번들 저장소와 수명·백업 정책 다름 → artifacts와 분리된 볼륨 [확인됨: 같은 주석].
- **고려한 대안**: postgres 승격 — 런타임 경로에 postgres 없고 단일 writer(console-api replica 1)라 sqlite로 충분, 서비스 추가는 YAGNI(기각) [확인됨: 런타임 코드에 postgres 미사용].
- **검토 요청 항목**: bind mount vs named volume 중 auditdb는 어느 쪽인지(영속·백업만 되면 무관, 권한만 정합).

---

## 결정 6: 보안 컨텍스트 이식 수준 — POC 최소, 하드닝은 백로그 명시

- **결정**: K8s의 보안 컨텍스트(runAsNonRoot uid 10001·readOnlyRootFilesystem·tmpfs `/tmp`·fsGroup·seccomp·cap drop)를 **POC 단계엔 최소만** 이식하고(대략 비-root 정도), readOnlyRootFilesystem·tmpfs·seccomp 등 하드닝은 **백로그로 명문화**한다. 부하테스트 목적에 보안 이식은 곁가지.
- **근거 + 출처등급**:
  - K8s 서빙/콘솔은 비-root·readOnlyRootFilesystem·tmpfs·fsGroup·seccomp·cap drop 전량 적용 [확인됨: `deploy/k8s/deployment.yaml` securityContext; `console-api.yaml`, `console-web.yaml` 동일 패턴].
  - 이식 마찰(bind mount 권한·tmpfs 마운트·uid 매핑)이 POC 목표(부하 측정)와 무관하게 시간 소모 [우리 결정].
  - console-web은 이미 nginx-unprivileged(uid 101)라 비-root 기본 [확인됨: `console-web/Dockerfile`].
- **고려한 대안**: 전량 이식 — 충실하나 POC 마찰↑, 부하 숫자와 무관(기각). 전량 생략 — 프로덕션 이관 시 재작업(부분 기각 — 최소는 유지).
- **미결/옵션**: "최소"의 정확한 경계(비-root만? cap drop 포함?)는 핸드오프. 하드닝 백로그 항목 목록화 필요. **단 "비-root 유지"를 택하면 bind mount 쓰기 권한 정합(M-3, 결정 2)이 필수 선결 — fsGroup 부재로 심링크 스왑·감사 append가 permission denied 날 수 있으므로 호스트 소유권/`user:` 정합을 핸드오프에서 반드시 확정한다.**
- **검토 요청 항목**: POC 보안 최소선이 감사 무결성(append-only 훅)·번들 무결성을 훼손하지 않는지.

---

## 결정 7: 기동 순서·healthcheck — depends_on + healthcheck, serving 우선

- **결정**: `depends_on` + healthcheck로 순서 보장 — (1) serving이 healthy(부팅 캘리브레이션 완료) 후, (2) console-api(lifespan에서 alias↔감사 화해), (3) front nginx는 web+api 뜬 뒤. serving healthcheck는 `/health`, 부팅 예산은 K8s startupProbe(~300s)에 대응하는 `start_period`로. **단 console-api의 serving 의존은 `service_healthy`가 아니라 `service_started`로 완화**한다(아래 ★가용성 결합 방지 참조).
- **★healthcheck는 이미지에 존재하는 도구로만 구현 (B-1)**: Compose healthcheck는 K8s `httpGet`(kubelet이 컨테이너 밖에서 수행)과 달리 **컨테이너 안에서 명령을 실행**한다. serving·console-api base 이미지는 `python:3.12-slim`이라 **curl·wget이 설치돼 있지 않다**(pip로 torch/fastapi/uvicorn/numpy/pandas/scipy/pydantic만 설치) [확인됨: `deploy/Dockerfile:4` `FROM python:3.12-slim`, `:12-15` pip 설치 목록에 curl/wget 없음]. 따라서 `test: ["CMD","curl",...]`류는 **항상 실패 → serving 영영 `unhealthy` → 뒤 서비스 기동 불가**(기동 백본 붕괴). healthcheck는 반드시 **이미지에 이미 있는 도구**로 짠다: slim에는 python 인터프리터가 있으므로 `test: ["CMD","python","-c","import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"]` 방식을 **의존으로 명문화**한다 [우리 결정]. **front-nginx는 예외** — alpine base라 busybox `wget`이 존재하므로 `wget -qO- .../` 방식이 유효(별개 처리) [확인됨: `console-web/Dockerfile` `nginxinc/nginx-unprivileged:alpine`; front-nginx도 alpine nginx 계열]. (curl을 이미지에 추가 설치하는 대안은 슬림 유지 원칙과 어긋나 기각 — python 내장 urllib로 충분.)
- **근거 + 출처등급**:
  - serving 첫 `/health`는 torch 로드 + 300-trial 캘리브레이션을 `_LOCK` 아래 동기 수행 → 부팅창이 김 [확인됨: `app.py` `_load_all`이 `synthetic.calibrate(n_trials=300)`; `deployment.yaml` startupProbe `failureThreshold:30 ×10s`].
  - console-api 부팅 lifespan이 라우팅 개시 전 alias↔감사 화해/seed [확인됨: `console-api.yaml` 주석 "부팅 lifespan … 화해/seed"].
  - 번들 seed는 수동 선작업이라(결정 2) `up` 시점 폴더는 이미 채워짐 → serving이 즉시 로드 가능, seed 순서는 Compose 밖 [우리 결정].
  - **★console-api의 serving 부팅 의존 완화 (M-2)**: console-api 부팅 lifespan은 **serving을 호출하지 않는다** — `lifespan`→`_reconcile_or_seed`는 alias(디렉토리)·감사 DB만 읽고 쓸 뿐, serving HTTP를 안 부른다 [확인됨: `service.py:157-161` `lifespan`이 `_reconcile_or_seed(fs)` 순회, `:135-150` 내부는 `deploy.active_version`·`audit`만 접근]. serving 호출은 오직 승인/롤백 시 `_propagate_and_confirm`뿐이다 [확인됨: `service.py:183-184` `_propagate_and_confirm`→`_trigger_reload`(`:167`)·`_get_health`(`:175`)]. 따라서 console-api를 serving `service_healthy`에 묶으면 **가용성 부당 결합**이 생긴다 — (a) UI가 serving의 ~300s 캘리브레이션 내내 불가용, (b) **seed 누락으로 serving이 stuck-unhealthy면 console-api가 영영 안 떠** 운영자가 콘솔로 진단조차 못 한다(결정 7의 "console-api graceful 빈 상태" 안전장치 무력화). 그러므로 console-api → serving 의존은 **`condition: service_started`로 완화**(또는 제거)한다 — serving이 뜨지 못해도 console-api는 떠서 "active version 없음"을 표시해 운영자가 seed 필요를 진단할 수 있어야 한다 [우리 결정].
- **고려한 대안**: depends_on 없이 restart 의존 — 크래시루프로 수렴하나 부팅 로그가 지저분(부분 기각). serving을 lazy(첫 요청 시 로드) — 첫 요청 latency에 300-trial이 껴 부하 측정 오염(기각 — 부팅 시 로드 유지).
- **★seed 미완 시 실패 비대칭 (B-R0-3)**: seed는 결정 2의 수동 선작업이나, 누락 시 두 서비스 거동이 갈린다 — **console-api는 graceful**(빈 alias·빈 audit이면 `_reconcile_or_seed`가 어느 분기도 안 타고 no-op 리턴) [확인됨: `service.py:135-150` — `alias_target=None & last=None`이면 무동작]. **그러나 serving은 graceful 아님** — 빈 alias 해석/번들 로드에서 예외 → `/health`가 반복 500(**stuck-unhealthy**: uvicorn 프로세스는 살아 있고 `/health`만 계속 실패) [검증 필요: `app.py` `_load_all`의 실제 예외 메시지·경로]. (용어 주의: 이건 진짜 crash-loop이 아니다 — 프로세스가 반복 종료·재기동하는 crash-loop은 아래 precondition explicit-exit + `restart`가 결합될 때에야 성립한다.) 따라서 serving에 **명시적 seed precondition 체크**(부팅 초입에 alias 존재 확인 → 없으면 "seed 필요: `uv run python -m scripts.h4.h4s_export_bundle vitals`" 명확 에러로 즉시 종료)를 두어, stuck-unhealthy(원인 불명확) 대신 **읽을 수 있는 실패**로 만든다 [우리 결정]. (체크 위치·메시지 구현은 핸드오프. seed 명령은 인자 없이 실행하면 기본 순회에서 `vitals_labs`까지 돌다 RuntimeError로 비-0 종료할 수 있으므로 **`vitals` 단일 인자**를 권장 — 결정 2·8 참조.)
- **미결/옵션**: `restart` 정책(`unless-stopped`) + healthcheck 실패 시 자동 재시작 여부 — Compose는 healthcheck unhealthy로 자동 재시작 안 함(autoheal 별도) [확인됨: Compose 동작]. POC는 restart만, hang 감지는 한계 명시. serving precondition 실패는 `restart: unless-stopped`와 결합 시 프로세스가 반복 종료·재기동하는 **진짜 crash-loop**가 되므로, precondition 실패는 **비재시작 종료 코드**로 구분할지 핸드오프에서 결정.
- **검토 요청 항목**: serving `start_period`가 최악 부팅(캘리브레이션 스로틀)을 커버하는지, console-api가 serving 없이도 부팅되는지(승인 시에만 serving 호출하므로 부팅 의존은 `service_started`로 느슨해야 — M-2), seed precondition 체크가 stuck-unhealthy를 명확 실패로 바꾸는지, healthcheck `test`가 이미지 내장 도구(python urllib)로만 짜였는지(B-1).

---

## 결정 8: featureset 단일(`vitals`) — POC 프로파일 목적

- **결정**: serving 인스턴스는 featureset 하나(`SERVE_FEATURESET=vitals`)로 뜬다. featureset 2개 동시 기동 안 함.
- **근거 + 출처등급**:
  - serving은 `SERVE_FEATURESET` env로 단일 featureset alias를 해석 [확인됨: `app.py:73,137` `os.environ.get("SERVE_FEATURESET","vitals")`].
  - 부하 프로파일(throughput·latency·메모리) 측정이 목적 → featureset 다중화 불요 [우리 결정].
  - **이 스택 serving = GRU 경로**(`sepsis.serve.app`). XGB는 별도 벤치 하니스(`xgb_app`)지 이 프로덕션 스택에 없음 → 부하테스트(나)는 GRU-only [확인됨: `deploy/Dockerfile` CMD `uvicorn sepsis.serve.app:app`; XGB는 `src/sepsis/serve/xgb_app.py` 벤치 전용].
- **★console↔serving featureset 불일치 (B-R0-4)**: console-api는 부팅 시 `CONSOLE_FEATURESETS` **전체를 순회**하며 화해/seed·`list_versions`를 노출한다 [확인됨: `service.py:159` `for fs in CONSOLE_FEATURESETS`]. **기본값은 이미 `["vitals"]` 단일**이라 기본 상태에선 serving(vitals)과 정합한다 [확인됨: `console/config.py:10-12` 기본 `["vitals"]`]. 유령 featureset 문제는 **`CONSOLE_FEATURESETS`를 env로 오버라이드해 `vitals_labs`까지 넣었을 때에만** 발생한다 — 그 경우 serving은 `vitals`만 띄우므로 **콘솔 UI에 active version 없는 featureset이 노출**되어 운영자를 혼란시킨다(승인 대상은 있는데 서빙 안 되는 유령 featureset). 따라서 POC는 **`CONSOLE_FEATURESETS` 오버라이드를 하지 않거나 `vitals` 단일로 유지**한다(serving과 정합). 두 featureset을 다 노출하려면 serving도 둘 띄워야 하며 그건 결정 8 범위 외 [우리 결정]. (`CONSOLE_FEATURESETS` 주입 방식은 env 오버라이드 가능한 상수 — `config.py`에서 확인됨.)
- **고려한 대안**: vitals+vitals_labs 두 serving — 부하 형태 이해에 불필요, 리소스만 2배(기각). console은 둘 순회하되 serving만 하나 — 유령 featureset 노출(기각, B-R0-4).
- **검토 요청 항목**: `vitals` alias 번들이 seed되는지(export가 vitals 생성 확인), `CONSOLE_FEATURESETS`가 serving featureset과 정합하는지, featureset 전환이 필요해질 때 재기동만으로 되는지.

---

## 결정 9: ★Compose 자원 제한 — 비-Swarm `cpus`/`mem_limit`, BLAS 캡·reload 메모리와 정렬 (B-R0-1·B-R0-2)

- **결정**: serving 컨테이너의 CPU·메모리를 **Compose 최상위 키(`cpus:`, `mem_limit:`)로** 건다. **`deploy.resources.limits`에 의존하지 않는다** — 최상위 키는 레거시 `docker-compose`(v1)·현행 `docker compose`(v2, Go 플러그인) 양쪽에서 모두 up에 적용되어 이식성이 가장 넓기 때문이다(값 적용 실측은 구현검증 항목 2로 확인). 값은 K8s 매니페스트와 정렬:
  1. **`cpus`** = BLAS 스레드 캡(`OMP/OPENBLAS/MKL/NUMEXPR_NUM_THREADS`)과 **같은 수**로 (예: 2). 부하테스트(나)의 코어 sweep은 이 `cpus` + 스레드 캡을 **함께** 바꾼다.
  2. **`mem_limit`** = **reload 순간 메모리 2배 창을 수용**하도록 (GRU RSS ~248MB × 2 + torch 런타임 + 여유 → 최소 1Gi 권장, K8s limits.memory=1Gi와 정렬).
- **근거 + 출처등급**:
  - **`deploy.resources` 적용은 Compose 버전에 따라 다르다** — 레거시 `docker-compose`(v1)는 `deploy.resources.limits`를 up에서 **무시**했다(Swarm 전용 취급)이나, 현행 `docker compose`(v2, Go 플러그인)는 `deploy.resources.limits.cpus/memory`를 up에서도 적용한다 [검증 필요: 설치 버전에서 실적용 대조 — 구현검증 2]. 이 버전 의존성을 피해 **최상위 `cpus:`/`mem_limit:`을 쓴다** — 이 최상위 키는 v1·v2 양쪽에서 up에 적용되므로 버전과 무관하게 안전하다 [우리 결정]. K8s `resources.limits`를 v1 위에서 `deploy.resources`로 순진하게 번역하면 컨테이너가 **호스트 전체 코어를 봄**(v2에선 걸리나 버전을 가정할 수 없음).
  - CPU 제한이 실제로 안 걸리면 BLAS 스레드 캡의 정렬 대상이 사라져 **작은 추론 배열에 스레드 과투입 = thrash** → 지난 벤치 "XGB 4배 느림" 아티팩트가 Compose에서 **재발** [확인됨: `deploy/k8s/deployment.yaml` env 주석 — "cgroup CPU limit이 스레드보다 작으면 thrash … 캘리브레이션 10s→60s+ 폭발"; `docs/reports/serving_benchmark_summary.md` 핵심 해석 1]. **이 프로젝트의 대표 교훈("측정 환경이 결과를 만든다")과 정면 충돌하는 함정**이므로 결정 수준에서 못박는다.
  - **reload 2배 메모리**: 핫스왑은 build-new-then-atomic-swap이라 옛/새 번들이 순간 공존 → RSS 2배 창 [확인됨: `app.py` `_load_all`이 `new_s`/`new_ds`를 지역 조립 후 `_S`/`_DS` 리바인드; 불변식 섹션]. mem_limit이 이보다 작으면 **부하 중 승인 시 OOM-kill** [우리 결정].
  - 부팅 캘리브레이션(300-trial)은 CPU-bound라 `cpus`가 너무 작으면 스로틀 → `start_period`(결정 7) 초과 위험 [확인됨: `deployment.yaml` startupProbe 주석]. `cpus`는 **부팅 여유 + 스레드 캡 정렬**을 동시에 만족해야 함(K8s는 requests.cpu 500m~limits.cpu 2로 폭을 뒀음 — Compose `cpus`는 단일값이라 캡 값 = 부팅 여유 값으로 통일).
- **고려한 대안**: (i) `deploy.resources` 사용 — v1에선 무시, v2에선 적용이라 버전 의존(설치 버전 가정 불가로 기각 — 최상위 키가 더 이식성 높음). (ii) 제한 아예 없음 — 호스트 전체 코어 노출로 thrash + reload OOM 방치, 부하 숫자 오염(기각). (iii) `docker compose` 대신 Swarm 모드 — 오케스트레이터 도입은 "K8s 안 쓰기로 한" 스코프와 어긋나고 과함(기각).
- **미결/옵션**: console-api/console-web/nginx 등 나머지 서비스의 제한 값 — 이들은 경합 유발이 적어 POC는 serving만 엄격 제한하고 나머지는 느슨하게 둘지 핸드오프에서. 부하 드라이버(나, Locust)의 CPU 격리(noisy neighbor)는 (나) 문서 소관 — 여기선 serving 제한만.
- **검토 요청 항목**: `cpus`/`mem_limit`이 실제로 `docker compose up`에서 적용되는지(무시 함정 회피 확인), `cpus` 단일값이 부팅 캘리브레이션 여유와 BLAS 캡 정렬을 동시에 만족하는지, reload 2배 창에서 OOM 안 나는지 실측.

---

## 전체 서비스 맵 (요약)

| 서비스 | 빌드 소스 | 포트 | 핵심 env | 볼륨 |
|---|---|---|---|---|
| serving | `deploy/Dockerfile` | 8000 (호스트 공개) | `SERVE_FEATURESET=vitals`, `LOG_LEVEL`, BLAS 스레드 캡 | artifacts(RO 성격) |
| console-api | `deploy/k8s/console/Dockerfile.api` | 8000 (내부) | **`SERVE_URL=http://serving:8000`**, `CONSOLE_AUDIT_DB_URL` | artifacts(RW) + auditdb |
| console-web | `console-web/Dockerfile` | 8080 (내부) | 없음(상대경로) | 없음 |
| front-nginx | nginx conf (신규) | 80 (호스트 공개) | 없음 | conf |
| prometheus | 기존 이미지 | 9090 | 타깃 `serving:8000` | prometheus.yml |
| grafana | 기존 이미지 | 3000 | 익명 Admin(데모) | provisioning |
| renderer | 기존 이미지 | (내부) | — | — |

> BLAS 스레드 캡(`OMP_NUM_THREADS` 등)은 CPU 할당과 정렬 — 작은 추론 배열에 스레드 과투입 경합 방지 + 부팅 캘리브레이션 스로틀 방지 [확인됨: `deployment.yaml` env 주석]. Compose에서도 유지하되 **CPU 제한은 최상위 `cpus:`로**(결정 9 — 최상위 키는 v1·v2 양쪽에서 up에 적용되어 이식성이 넓다).

## 불변식 (환자 안전 분리 — 절대 불변)

- **예측/추론 로직**: `predict()`·`_row_from`·응답 dict·`serve_predict_latency` 관측 불변 [확인됨: `app.py`].
- **번들 원자성**: 한 version dir = 모델+stats+τ+reference 한 세트, 롤백 시 함께 복원 [확인됨: `bundle.py`, export docstring].
- **감사 append-only**: UPDATE/DELETE 차단 훅 불변 [확인됨: `audit.py` event 훅].
- **모델 교체 = 무중단 핫스왑**: build-new-then-atomic-rebind, readers 무락 → 교체 중 서비스 중단 없음(대가: 순간 메모리 2배 + 교체 자체 수십 초) [확인됨: `app.py` `_load_all`/`state`/`admin_reload`]. 이 스택은 이 성질을 훼손하지 않는다. **단 순간 메모리 2배는 `mem_limit`이 수용해야 함(결정 9) — 아니면 부하 중 승인 시 OOM.**

## 알려진 한계 (정직)

- **단일 호스트** — network 잔차는 minikube 단일노드와 동일 토폴로지, 멀티노드 대비 network 성분 과소. 진짜 격리·network 실측은 (나) 이후 별도 머신/코어 pinning으로 [우리 결정].
- **replica 1 강제** — in-memory per-patient hidden state. 무중단 *코드* 배포·수평 확장은 Redis 상태 외부화 선행(로드맵) [확인됨: `deployment.yaml` replicas:1 주석].
- **무인증 콘솔** — `/console` 쓰기 엔드포인트 무인증 노출(M4 빚) [확인됨: `ingress.yaml` M4 주석].
- **무인증 관측 대시보드** — grafana(호스트 3000)·prometheus(호스트 9090)도 무인증으로 호스트 공개된다(콘솔과 동일 등급 노출). grafana는 데모용 익명 Admin, prometheus는 인증 없음 [확인됨: 서비스 맵 — grafana 3000·prometheus 9090 호스트 공개]. POC 한계로 명시, 하드닝은 백로그(결정 6).
- **수치 재현 = torch/numpy 버전 고정 전제** — serving 스택은 **GRU-only**(이미지에 xgboost·mlflow·xgb 미설치) [확인됨: `deploy/Dockerfile:3` 주석 "no MLflow/pandas/xgboost in the image", pip 설치 목록에 xgboost 없음]. 재현성은 torch·numpy 등 서빙 의존 버전을 uv.lock과 일치시키는 것에 달림 [확인됨: `deploy/Dockerfile:9-15` torch==2.12.1·numpy==2.5.0 고정 + "uv.lock 과 정확히 일치" 주석]. (v2 이전판의 "best_iter/xgboost 3.3.0" 서술은 이 GRU 스택엔 해당 없음 — xgboost는 별도 벤치 하니스 소관이라 삭제, M-4.)

## 레드팀 종합 검토 요청

1. **PVC→bind mount 번역**이 전파 사슬(console alias swap → serving reload)을 온전히 보존하는가? 특히 상대 심링크가 WSL2 bind mount 경계를 넘어 해석되는지 [검증 필요].
2. ~~**`SERVE_URL` 배선 누락**이 유일한 silent 단절점인가~~ → **R0 해소**: `service.py:167,175` 두 곳뿐, 다른 localhost 기본 없음 [확인됨].
3. **기동 순서**가 seed 미완/캘리브레이션 지연 등 최악 케이스에서 stuck-unhealthy 대신 graceful 실패하는가? → **R0 부분 해소**: console-api graceful, **serving은 precondition 체크 신설로 대응**(결정 7, B-R0-3). healthcheck는 이미지 내장 도구(python urllib)로만 짜야 함(B-1). 실제 예외 메시지 [검증 필요].
4. **보안 최소선(결정 6)**이 감사·번들 무결성을 훼손하지 않는가.
5. (가)가 (나)를 얹을 때 serving 재작성을 강제하지 않는가(결정 1 확장성).
6. **★자원 제한(결정 9)**이 `docker compose up`에서 실제 적용되는가(`deploy.resources` 무시 함정 회피), `cpus`가 BLAS 캡·부팅 여유를 동시에 만족하는가, `mem_limit`이 reload 2배 창을 수용하는가 [검증 필요: 실측].
7. **featureset 정합(결정 8)**: `CONSOLE_FEATURESETS`가 serving과 일치해 유령 featureset이 콘솔에 안 뜨는가.