# Console 구현 핸드오프 B (명세부) — 프론트: React 콘솔 + Grafana 2패널 + K8s 배포

> **전제**: `design/console/decisions.md`(설계부) 통과 + 핸드오프 A(백엔드) 구현 완료(52/52 GREEN, `97c2bd5`). 본 문서는 그 위에 얹는 표현·관측·배포 계층을 명세한다.
> **워크플로우**: 검토(`handoff_frontend_review.md`) 통과 → 구현. React/Grafana는 TDD보다 **와이어프레임·API 계약 일치**가 기준(UI는 단언보다 계약 정합). K8s YAML은 **사용자 직접 작성**(CKA 학습) — 본 문서는 골격·라우팅 규칙·이유만 제공.
> **대상(신규)**: `console-web/`(Vite+React), `deploy/grafana/dashboards/serving_slo.json`, `deploy/k8s/console/*.yaml`.
> **재활용(변경 없음)**: 핸드오프 A의 `/console` API, 서빙 `metrics.py`, 기존 `deploy/grafana/provisioning/*`.
> **상태**: 명세부 v3 (Round 1 blocker 2·major 1·minor 4 + Round 2 blocker 1(BR2-1 롤백 게이팅)·minor 1(mr2-1 active=null 표기) 보완 반영; `handoff_frontend_review.md` 참조).

## 코드 현황 (구현 시작점 — 프론트가 소비하는 실제 계약)

핸드오프 A 구현 기준(`src/sepsis/console/api.py`):

| 엔드포인트 | 요청 | 응답 |
|---|---|---|
| `GET /console/versions?fs=` | — | `{featureset, active, versions:[{version, bucket, ready, gate_passed, bholdout_util, has_mlflow}]}` |
| `GET /console/versions/{version}?fs=` | — | `{version, bucket, ready, gate{no_regression·bholdout_util·new_aval_util·old_aval_util·...·eps·cross_site_claim·validated_at}, retrain{epochs·val_loss·b_split_seed·n_*·run_id·git_commit}, meta{featureset·tau·trained_on}, mlflow_link}` |
| `GET /console/audit?event_type=&gate_passed=&since=&fs=` | — | `[{id·ts·event_type·featureset·gate_passed·from_version·to_version·run_id·git_commit·actor_unverified·verified_subject·reason}]` |
| `POST /console/approve` | `{fs, version, actor, reason}` | `{event_id, prev, active, propagation}` |
| `POST /console/rollback` | `{fs, version, actor, reason}` | `{event_id, prev, active, propagation}` |

- **에러 코드**: 422 = `ValueError`/`FileNotFoundError`(미완성·교차-fs·REGRESSED). 프론트는 이 코드로 분기. 403 = `PermissionError`(미승인)은 **콘솔 경로에서 dead path** (mn3): `service.approve`가 `deploy.swap(..., approved=True)`로 항상 True 고정(`service.py:93`), rollback은 swap 미경유 → `PermissionError`(`deploy.py:84-85`)가 발생할 수 없다. 403 핸들러는 무해한 방어적 폴백일 뿐 "미승인 차단"의 실제 작동 방어선이 아니다.
- **`propagation`** = `"confirmed"` | `"pending"`. 서빙 메모리 반영 확인 결과(결정 2-A).
- **`bucket`** = `champion`|`challenger`|`archived`|`incomplete`.
- **읽기/쓰기 `version` 비대칭 (B1)** `[확인됨]`: **읽기 응답**(`list_versions.version`·`active`, `get_version_detail.version`)은 `gru_<fs>@` 접두가 **제거된 순수 버전**("champ")이다(`service.py:211,218,237`의 `_strip_prefix`). 그러나 **쓰기 요청**(`approve`/`rollback`)의 `version`은 **디렉토리명**(`gru_<fs>@<v>`)이어야 한다 — `service.approve`/`rollback`이 첫 줄에서 `_require_consistent(fs, version)`로 `version.startswith(f"gru_{fs}@")`를 강제하며 아니면 `ValueError`→422(`service.py:38-40,84,104`). `api.py:20` 주석도 `WriteRequest.version` = "버전 디렉토리명(gru_<fs>@<v>, B1)"로 못 박음. **프론트는 쓰기 직전 순수 버전에 `gru_${fs}@`를 재부착해야 한다**(아래 구현 1 항목 5 참조).
- 서빙 메트릭(`serve/metrics.py`): `serve_predict_requests_total`·`serve_predict_latency_seconds`(Histogram)·`serve_alarms_total`·`serve_health_requests_total`·`serve_pred_prob`·`serve_input_feature_value`·`serve_input_missing_total`. (에러 카운터 **없음** — G3 한계로 후술.)
- 기존 Grafana: `deploy/grafana/dashboards/drift.json`(드리프트 6패널) + `provisioning/{datasources/prometheus.yaml, dashboards/drift.yaml}`. 신규 대시보드는 같은 provisioning 메커니즘에 얹음.

---

## 구현 1: React 통합 콘솔 (Vite) — `console-web/`

결정 1(와이어프레임)·결정 3(게이트 수치 크게). 스택 = **Vite + React + TypeScript**, 상태는 React 훅(useState/useEffect)만(무거운 상태 라이브러리 불필요 — 단일 화면), HTTP는 `fetch`. API base = **상대경로 `/console/*`**(Ingress 동일출처, 구현 3) — `VITE_API_BASE` 환경변수, 기본 `""`(동일출처).

### 컴포넌트 구조 (와이어프레임 매핑)

```
App
 ├─ StatusBar              상단 활성(champion) 상태바: active 버전(읽기=`list_versions.active`) + 전파상태 배지(쓰기 직후 transient, MJ1 주석 참조). active=null(심링크 소실)이면 "활성 alias 없음(심링크 소실)" 명시 표기(mr2-1)
 ├─ VersionList            list_versions.versions를 버킷 정렬: champion→challenger→archived→incomplete
 │   └─ VersionRow         접힌 행: version·bucket 배지·gate_passed 신호·bholdout_util 헤드라인·MLflow 아이콘(has_mlflow)
 │       └─ VersionDetail  행 클릭 시 펼침 — get_version_detail 호출(lazy)
 │           ├─ GatePanel    게이트 수치 크게 + 판정 보조(결정 3): util 3종 大, PASS/REGRESSED 배지 小
 │           ├─ RetrainPanel epochs·val_loss·seed·n_*·git_commit
 │           ├─ Actions      승인(challenger 한정) / 롤백(archived 한정) 버튼(+ ConfirmDialog) — 버킷별 노출, BR2-1
 │           ├─ MlflowLink   mlflow_link 있으면 링크, 없으면 폴백 안내(6-A)
 │           └─ AuditTrail   이 버전 관련 감사 — 서버는 fs 필터만, 버전 단위는 클라가 거름(mn2)
 └─ ConfirmDialog          actor 입력 + reason → POST approve/rollback → propagation 결과 표시
```

### 프론트 고유 계약 (백엔드가 강제하지만 UI도 반영해야 하는 것)

1. **REGRESSED·미완성 승인 버튼 비활성 (M3 이중 게이트, mn1)**: 승인 버튼은 **`gate_passed !== true`이거나 `ready !== true`일 때** `disabled` + 툴팁("게이트 미통과/미완성 — 승인 불가"). `gate_passed`는 `bucket=="incomplete"`면 `null`이므로(`service.py:214`) `=== false`만 보면 `null`이 통과해 버튼이 활성화되는 헛클릭을 부른다 — 그래서 `!== true`(또는 `!ready`)로 사전차단한다. 백엔드는 미완성을 `_require_ready`→`FileNotFoundError`→422(`service.py:78-79,86`)로 막고 프론트가 그 422를 표면화하므로 계약은 닫히지만, UI에서 먼저 차단해 헛클릭을 막는다.
2. **전파 대기/실패 구분 표시 — 쓰기 직후 transient (결정 2-A-c, MJ1)**: `propagation` 값은 **approve/rollback 응답에서만** 온다(`service.py:100,112`). 읽기 엔드포인트(`list_versions`)는 `active`만 줄 뿐 `propagation`/serve-sync 상태를 **반환하지 않는다**(`service.py:218`). 따라서 StatusBar 전파 배지는 **쓰기 직후의 transient 상태**다 — approve/rollback 응답 `propagation`이 `"confirmed"`가 아니면 그 버전을 **"전파 대기/실패"**(주황 배지)로 구분 표시(숨기지 않음). **페이지 새로고침/새 탭(React state 소실) 후에는 전파 배지를 채울 읽기 출처가 없으므로 배지를 비우거나 "상태 미상"으로 표기**하고, `active`만 `list_versions`로 재확인한다. (프론트는 same-origin `/console/*`만 호출하므로 serve `/health`를 직접 못 본다 — 분리 원칙.) 로드 시 전파 상태가 필요하면 console-api에 `/console/serve-sync?fs=`(serve `/health` 프록시) 읽기 엔드포인트를 추가해야 하나 이는 핸드오프 A 백엔드 변경 → **범위 외 권고** `[검증 필요]`. 재폴링 = `GET /console/versions` 재호출은 **active 재확인만** 제공(전파 상태 아님).
3. **1일차 archived 비어있음 명시 (mn1)**: archived 버킷이 비면 "콘솔 도입 이전 이력 없음 — 거짓 복원하지 않음" 안내. 빈 목록을 오류로 보이지 않게.
4. **cross-site 정직성 (코드 강제와 정합)**: GatePanel에 `cross_site_claim`을 표시 — `false`면 "in-distribution 검증 (cross-site 일반화 주장 아님)" 명시. 게이트 수치를 과대해석하지 않게.
5. **버전 식별자 = 디렉토리명, 쓰기 직전 접두 재부착 (B1)** `[확인됨]`: 읽기 응답의 `version`은 stripped 순수 버전("champ")이지만, **쓰기(`approve`/`rollback`) 요청의 `version`은 디렉토리명 `gru_${fs}@${version}`이어야 한다**. 백엔드 `_require_consistent`가 `gru_<fs>@` 접두를 강제하기 때문(`service.py:38-40`) — stripped 값을 그대로 보내면 `ValueError("version 'champ' not in featureset 'vitals'")`→422로 **모든 쓰기가 실패**한다. 따라서 프론트는 ConfirmDialog에서 POST를 만들 때 stripped 표면값에 `gru_${fs}@`를 **재부착해 디렉토리명으로 전송**한다. (읽기는 stripped, 쓰기는 dir name — 비대칭이 백엔드 계약이다.)
6. **AuditTrail "이 버전 관련" 필터는 클라이언트 측 (mn2)** `[확인됨]`: `/console/audit` 쿼리 파라미터는 `event_type/gate_passed/since/fs`뿐이라(`api.py:46`) **버전 단위 서버 필터가 없다** — `fs` 필터는 featureset 전체 이벤트를 준다. 따라서 "이 버전 관련"은 프론트가 받아온 행을 `from_version === dirName || to_version === dirName`으로 **클라이언트가 직접 거른다**. 이때 비교 키는 audit 행의 `from_version`/`to_version`이 **디렉토리명**(`gru_<fs>@<v>`)이므로, B1의 `toDirName(fs, version)`으로 만든 디렉토리명과 매칭한다(읽기 리스트의 stripped 버전을 그대로 비교하면 안 됨 — 재부착 키와 정합).
7. **롤백 버튼은 `bucket === "archived"`(과거활성)에만 활성 — 프론트가 1차 방어선 (BR2-1)** `[확인됨]`: **백엔드 롤백 경로에는 하드게이트가 없다.** `service.rollback`(`service.py:103-112`)은 `_require_consistent`(fs 접두)만 검사하고 `_require_ready`도 validation 게이트도 호출하지 않으며, `deploy.rollback`(`deploy.py:93-95`)은 `set_alias`만 수행 — `approved`·`no_regression`·`.ready` 어떤 가드도 없다(하드게이트는 **승인 경로 `swap`에만**, `deploy.py:86-87`). 따라서 REGRESSED challenger(게이트 무관 자재화·`.ready` 부여, `deploy.py:42-43,59-67` → `_classify`에서 `challenger`·`gate_passed=false`, `service.py:189-197,214`) 행이나 미완성(`.ready` 없는 `incomplete`) 행에서 롤백을 누르면 **손상·미검증 모델이 활성화**돼 결정 3·M3의 "REGRESSED 비승격"(`decisions.md:83·203`)이 롤백 경로로 뚫린다. 설계 의도(`decisions.md:124` "롤백 대상 = 과거 검증된 champion")를 강제하는 **유일한 지점이 프론트**다 — 롤백 버튼은 **`bucket === "archived"`(과거 한때 활성, `_classify`의 `past_active` 매치) 버전에만** 노출/활성하고, `champion`(현재활성)·`challenger`·`incomplete`에는 `disabled`(승인 게이팅과 대칭). **H4r 구현 완료(콘솔 경계)** `[확인됨]` (커밋 `790a285`): 백엔드 `service.rollback`이 이제 동일한 `_classify` 기준으로 롤백 대상이 `archived`(과거활성)인지 강제하고, 아니면 `ValueError`→422를 던진다(테스트 `tests/console/test_rollback.py::test_rollback_rejects_non_archived_target`). 따라서 **콘솔 경로(`POST /console/rollback`)는 프론트 사전차단 + 백엔드 422의 이중 방어**가 됐고, 프론트는 더 이상 *유일* 방어선이 아니다(헛클릭 방지·UX 1차 차단 역할은 유지). **H4r 대칭화도 구현 완료** `[확인됨]`: `deploy.rollback`이 이제 `swap`처럼 `*, approved: bool` 가드(`approved is not True`면 `PermissionError`)를 갖고 prev(이전 활성 디렉토리명)를 반환한다(`deploy.py`; 테스트 `tests/console_prep/test_deploy_rollback_symmetry.py`). 따라서 콘솔 API를 우회해 `deploy.rollback`을 직접 import 호출하는 경로도 무승인이면 차단된다. `service.rollback`은 승인 경계로서 `approved=True`를 넘긴다(`swap`에 `approved=True`를 넘기는 `approve`와 대칭). 단 이 가드는 "승인 여부"만 보고 "대상이 archived인지"는 보지 않으므로, archived 강제(REGRESSED 비승격)의 권위는 여전히 콘솔 경계(`service.rollback`의 `_classify` 게이트)다.

### API 클라이언트 (`console-web/src/api.ts`)

```ts
const BASE = import.meta.env.VITE_API_BASE ?? "";
// B1: 읽기는 stripped 순수버전, 쓰기는 디렉토리명. 쓰기 직전 접두 재부착.
const toDirName = (fs: string, version: string) =>
  version.startsWith(`gru_${fs}@`) ? version : `gru_${fs}@${version}`;   // 이미 dir name이면 그대로(이중접두 방지)
export const getVersions   = (fs: string) => fetch(`${BASE}/console/versions?fs=${fs}`).then(j);
export const getDetail     = (fs: string, v: string) => fetch(`${BASE}/console/versions/${v}?fs=${fs}`).then(j);  // v = stripped(읽기 경로)
export const getAudit      = (q: AuditQuery) => fetch(`${BASE}/console/audit?${qs(q)}`).then(j);
export const approve       = (fs: string, v: string, actor: string, reason: string) =>
  post(`${BASE}/console/approve`, { fs, version: toDirName(fs, v), actor, reason });   // 422 분기
export const rollback      = (fs: string, v: string, actor: string, reason: string) =>
  post(`${BASE}/console/rollback`, { fs, version: toDirName(fs, v), actor, reason });
// BR2-1/H4r: service.rollback이 롤백 대상 archived를 강제(_classify→ValueError→422, 커밋 790a285).
//   deploy.rollback도 H4r 대칭화됨: approved 가드 + prev 반환(swap과 대칭). service가 approved=True 전달.
//   롤백 버튼은 UI에서 bucket==="archived" 행에만 노출/활성해야 한다 — challenger/incomplete/champion 비활성.
//   프론트는 UX 1차 차단(헛클릭 방지) + 백엔드 422 = 이중 방어.
// post(): !res.ok면 res.status로 분기 — 422=게이트/미완성/교차-fs 메시지 표면화.
// 403(PermissionError)은 콘솔 경로에서 dead path (mn3): service.approve가 deploy.swap에 approved=True 고정(service.py:93),
//   rollback은 swap 미경유 → PermissionError("approved is not True", deploy.py:84-85) 발생 불가. 핸들러는 방어적 폴백일 뿐 작동 방어선 아님.
```

---

## 구현 2: Grafana 2패널 — `deploy/grafana/dashboards/serving_slo.json`

기존 drift 대시보드와 **분리한 신규 대시보드** `Sepsis — Serving SLO & Health`. 기존 provisioning(`dashboards/drift.yaml` 패턴) 복제로 자동 로드. 데이터소스 = 기존 Prometheus.

### G3 — 서빙 SLO

| 패널 | PromQL | 타입 |
|---|---|---|
| 지연 p95/p99 | `histogram_quantile(0.95, sum(rate(serve_predict_latency_seconds_bucket[5m])) by (le))` / 0.99 | timeseries |
| 처리량(req/s) | `rate(serve_predict_requests_total[5m])` | timeseries |
| 알람률 | `rate(serve_alarms_total[5m]) / rate(serve_predict_requests_total[5m])` | stat |

- **한계 (정직)** `[검증 필요]`: **에러율 패널은 현재 메트릭으로 불가** — `serve/metrics.py`에 에러 카운터가 없다. 완전한 SLO(가용성·에러버짓)를 원하면 서빙에 `serve_predict_errors_total` Counter를 추가해야 하며, 이는 콘솔 밖 서빙 변경이라 **범위 외(권고)**. 본 패널은 지연·처리량·알람률로 한정하고 에러율 공백을 명시.

### G4 — 헬스

| 패널 | PromQL | 타입 |
|---|---|---|
| 서빙 up/down | `up{job="sepsis-serving"}` | stat (1=up) |
| predict 생존신호 | `rate(serve_predict_requests_total[1m])` | timeseries |
| health 스크레이프 | `rate(serve_health_requests_total[1m])` | timeseries |

- **`up{job=}` 라벨은 Prometheus scrape 설정 종속** `[검증 필요]` — job 이름은 실제 scrape config에 맞춤.
- **활성 모델 run_id는 Grafana 패널이 아니다 (정직한 경계)**: run_id는 `/health` JSON 필드지 Prometheus 메트릭이 아니다. **활성 버전 표시는 React 콘솔 StatusBar(`list_versions.active`)의 몫** — Grafana는 시계열 메트릭만. (원하면 서빙에 `serve_build_info{run_id=...} 1` info-메트릭 추가 가능하나 범위 외.)

---

## 구현 3: K8s 배포 — `deploy/k8s/console/` (Ingress, 갈래 ㄱ) **[YAML = 사용자 직접 작성, CKA]**

결정 2(분리). 3 Deployment 구조. **본 절은 골격·필드·이유·라우팅 규칙만** — YAML은 사용자가 손으로(serve 기존 매니페스트 참고). 학습 가치 소진 시 "풀버전 줘".

### 토폴로지

```
[Ingress: console.<host>]
   ├─ path "/"          → [Service console-web] → [Deploy console-web: nginx + 빌드된 React]  (replicas 1)
   └─ path "/console"   → [Service console-api] → [Deploy console-api: FastAPI sepsis-console] (replicas 1)
```

브라우저는 `console.<host>` 동일출처만 봄 → **CORS 없음**. React가 `/console/*`를 상대경로로 부르면 Ingress가 console-api로 라우팅.

**단일 featureset 토폴로지 (mn4)** `[확인됨]`: serve는 `SERVE_FEATURESET`(기본 vitals) **하나만** 로드/리로드한다(`app.py:72,132,140`). 따라서 `_propagate_and_confirm(fs)`는 단일 serve의 그 fs만 리로드한다. 본 핸드오프는 **vitals MVP 한정**(`CONSOLE_FEATURESETS=["vitals"]`, `config.py:10-12`)으로 정합한다. 다중 featureset로 확장하려면 **fs당 serve+console-api 1쌍**(각자 `SERVE_FEATURESET`/`SERVE_URL` 고정)으로 토폴로지를 복제해야 하며, 단일 serve에 여러 fs를 태우는 것은 범위 외 `[검증 필요]`.

### 각 조각이 필요로 하는 것 (직접 작성 시 체크리스트)

- **console-api Deployment**: image=콘솔 이미지(`api:app` uvicorn), port 8000, replicas **1**(직렬화 경계 전제 — 결정 7-1, serve의 "replicas=1 REQUIRED" 주석 패턴 따름). 환경:
  - **번들 저장소 공유 — console-api는 `ARTIFACTS_DIR`를 읽지 않는다** `[확인됨]`: console 경로의 ARTIFACTS는 `deploy.ARTIFACTS = C.ROOT/"deploy"/"artifacts"`로 **하드코딩, env 미참조**(`deploy.py:27`; `service.ARTIFACTS = deploy.ARTIFACTS`, `service.py:25`). 따라서 console-api에 `ARTIFACTS_DIR`를 줘도 **무시된다**. PVC는 반드시 **코드가 읽는 고정 경로 `<C.ROOT>/deploy/artifacts`**에 마운트해야 한다(`C.ROOT`=설치 코드 기준 repo 루트 = `config.py:12`의 `parents[2]`; 컨테이너 WORKDIR가 `/app`이면 `/app/deploy/artifacts` — 실제 경로는 이미지 빌드의 WORKDIR에 종속 `[검증 필요]`). **serve는 반대로 `ARTIFACTS_DIR`를 읽으므로**(`app.py:39`) serve 쪽은 `ARTIFACTS_DIR`로 그 동일 마운트를 가리키거나 동일 고정 경로에 마운트한다. 비대칭이 코드 계약이다 — 양쪽에 같은 env를 주는 대칭 가정은 silent 단절을 부른다(아래 "공유 저장소 의존" 참조). minikube는 hostPath/PVC, 정확한 StorageClass는 `[검증 필요]`.
  - `SERVE_URL=http://sepsis-serving:8000` → `_propagate_and_confirm`이 `/admin/reload`·`/health`를 서빙 Service DNS로 호출(핸드오프 A의 전파 확인이 이 주소를 씀).
  - 감사 DB 영속(`console_audit.db` 또는 PVC). readiness/liveness probe = 가벼운 GET(예: `/console/versions?fs=vitals`, 단 빈 클러스터 200 보장 확인).
- **console-web Deployment**: image=nginx + `console-web` 빌드 산출물(`vite build` → `dist/`를 nginx 정적 루트로). replicas 1(정적이라 stateless, 필요시 증설). nginx는 SPA 폴백(`try_files ... /index.html`)만.
- **Service 2개**: 각 Deployment 앞 ClusterIP(이름 = `console-web`·`console-api`, serve의 `service.yaml` 패턴).
- **Ingress 1개 (새로 배우는 것)**: host `console.<host>`, 2 path rule(`/`→console-web, `/console`→console-api). minikube는 `minikube addons enable ingress`로 nginx-ingress 활성 필요 — `[검증 필요]`(환경 의존).

### 공유 저장소 의존 (가장 중요, 못 박음)

`console-api`와 `serve`는 **같은 `deploy/artifacts`(alias 심링크)를 봐야** 콘솔 swap이 서빙에 반영된다. 별도 볼륨이면 콘솔이 바꾼 alias를 서빙이 못 본다 → 전파 사슬 단절. 따라서 **동일 PVC를 양쪽 Deployment가 마운트**(ReadWriteMany 또는 단일노드 hostPath). 이건 결정 2의 "공유 자원 = 번들 저장소 + 감사 DB"의 K8s 구현.

**비대칭 마운트 경로 (B2, 정직하게 못 박음)** `[확인됨]`: 두 컨테이너의 경로 해석이 **다르다**.
  - **console-api**: `deploy.py:27`이 `ARTIFACTS = C.ROOT/"deploy"/"artifacts"`로 하드코딩, env 미참조. → PVC를 반드시 **이 고정 경로**(`<C.ROOT>/deploy/artifacts`, 예: WORKDIR `/app`이면 `/app/deploy/artifacts`)에 마운트. `ARTIFACTS_DIR`를 줘도 무시된다.
  - **serve**: `app.py:39`이 `ARTIFACTS = Path(os.environ.get("ARTIFACTS_DIR", C.ROOT/"deploy"/"artifacts"))`. → `ARTIFACTS_DIR`로 console-api의 고정 마운트 경로를 가리키거나, env 없이 동일 고정 경로에 마운트.
  - **함정**: 양쪽에 `ARTIFACTS_DIR=/mnt/shared`를 주고 PVC를 `/mnt/shared`에만 마운트하면 — serve는 `/mnt/shared`를, console-api는 여전히 `<C.ROOT>/deploy/artifacts`를 읽어 **다른 디렉토리**를 보게 되고 swap이 서빙에 전파되지 않는다(silent 단절, 결정 2-A 일관성 붕괴). **반드시 두 컨테이너가 같은 PVC를 같은 실효 경로로 보게** 검증할 것.
  - **대안(범위 외, 교차단계 의존)**: `deploy.py`도 `ARTIFACTS_DIR`를 읽도록 보강하면 대칭이 되나, 이는 핸드오프 A/H4 코드 변경이라 본 핸드오프 범위 밖 — 별도 결정 필요 `[검증 필요]`.

`[검증 필요]`: minikube 단일노드는 hostPath로 족하나, 멀티노드는 RWX PVC 필요.

---

## 성공 기준 (구현 검증 대상)

1. **콘솔 렌더**: `list_versions` 응답으로 StatusBar(active) + 버킷 정렬 리스트가 그려진다. `active=null`(심링크 소실, `service.py:44-45,218`)이면 빈 상태바 대신 "활성 alias 없음(심링크 소실)" 명시 표기(mr2-1). 행 클릭 시 `get_version_detail` lazy 호출로 게이트 수치 크게·판정 보조(결정 3)가 펼쳐진다.
2. **REGRESSED·미완성 차단(M3, mn1)**: `gate_passed !== true`(false 또는 incomplete의 null) 또는 `!ready` 버전은 **승인** 버튼 비활성 + 백엔드 422 메시지 표면화(이중 게이트).
2b. **롤백 대상 게이팅(BR2-1)**: **롤백** 버튼은 `bucket === "archived"`(과거활성) 버전에만 활성 — `challenger`·`incomplete`·`champion`(현재활성)에는 `disabled`. 프론트 게이팅이 헛클릭 방지·UX 1차 차단이고, 이것이 결정 3·M3의 "REGRESSED 비승격"(`decisions.md:83·203·124`)을 롤백 경로에서도 지킨다. **H4r 구현됨**(커밋 `790a285`): 백엔드 `service.rollback`도 동일 `_classify`로 롤백 대상 archived를 강제→`ValueError`→422 → 콘솔 경로는 **프론트 + 백엔드 이중 방어**. (`deploy.rollback` 원함수도 H4r 대칭화 구현됨 — `approved` 가드 + prev 반환, 직접 import 우회도 무승인 차단.)
3. **전파 표시(2-A-c)**: approve/rollback 후 `propagation!="confirmed"`면 "전파 대기/실패"로 구분 표시, 재폴링 제공. `"confirmed"`면 활성 확정 표시.
4. **정직성 UI**: archived 빔 → "이전 이력 없음(거짓 복원 안 함)" 안내(mn1). GatePanel에 `cross_site_claim=false` 시 "cross-site 주장 아님" 명시.
5. **버전 식별자(B1)**: 쓰기 요청 `version`이 **디렉토리명 `gru_<fs>@<v>`**으로 전송된다 — 프론트가 stripped 표면값에 `gru_${fs}@`를 재부착(`toDirName`)해 `approve`/`rollback`이 `_require_consistent`(`service.py:38-40`)를 통과한다. (읽기 응답은 stripped, 쓰기는 dir name — 비대칭 계약.)
6. **G3/G4 대시보드**: 신규 `serving_slo.json`이 provisioning으로 자동 로드되고, 지연 p95/p99·처리량·알람률(G3)·up/생존신호(G4)가 실제 메트릭에 걸린다. 에러율 공백이 명시된다.
7. **K8s 라우팅·공유저장소 비대칭(B2)**: Ingress가 `/`→web, `/console`→api로 라우팅해 CORS 없이 동작. console-api·serve가 **같은 artifacts PVC**를 공유해 콘솔 swap이 서빙에 전파된다 — 단 **경로 해석이 비대칭**: console-api는 `ARTIFACTS_DIR`를 무시하고 고정 `<C.ROOT>/deploy/artifacts`를 읽으므로(`deploy.py:27`) PVC를 그 고정 경로에 마운트하고, serve는 `ARTIFACTS_DIR`(`app.py:39`)로 동일 실효 경로를 가리키게 한다. 양쪽이 같은 디렉토리를 실제로 보는지 검증. console-api replicas=1.
8. **분리 불변**: 콘솔(web/api)이 추론 서빙의 환자별 상태·예측 경로를 건드리지 않는다(결정 2).

## 범위 외 (명시)

- `serve_predict_errors_total`·`serve_build_info` 추가 → 서빙 코드 변경, 권고만(G3 에러율·G4 run_id 패널 원할 시).
- 정확한 PVC StorageClass·minikube ingress addon·`up{job=}` 라벨 → 배포환경 종속 `[검증 필요]`.
- 인증/SSO·콘솔 접근제어 → 후속(핸드오프 A M4와 동일 라인).
- React E2E 테스트 자동화 → 본 단계는 와이어프레임·계약 일치로 검증, 자동 E2E는 후속.
- K8s YAML 실제 파일 → 사용자 직접 작성(CKA). 본 핸드오프는 골격·라우팅·이유.