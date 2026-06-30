# Console 구현 핸드오프 B (명세부) — 프론트: React 콘솔 + Grafana 2패널 + K8s 배포

> **전제**: `design/console/decisions.md`(설계부) 통과 + 핸드오프 A(백엔드) 구현 완료(52/52 GREEN, `97c2bd5`). 본 문서는 그 위에 얹는 표현·관측·배포 계층을 명세한다.
> **워크플로우**: 검토(`handoff_frontend_review.md`) 통과 → 구현. React/Grafana는 TDD보다 **와이어프레임·API 계약 일치**가 기준(UI는 단언보다 계약 정합). K8s YAML은 **사용자 직접 작성**(CKA 학습) — 본 문서는 골격·라우팅 규칙·이유만 제공.
> **대상(신규)**: `console-web/`(Vite+React), `deploy/grafana/dashboards/serving_slo.json`, `deploy/k8s/console/*.yaml`.
> **재활용(변경 없음)**: 핸드오프 A의 `/console` API, 서빙 `metrics.py`, 기존 `deploy/grafana/provisioning/*`.
> **상태**: 명세부 v1 (레드팀 미검토).

## 코드 현황 (구현 시작점 — 프론트가 소비하는 실제 계약)

핸드오프 A 구현 기준(`src/sepsis/console/api.py`):

| 엔드포인트 | 요청 | 응답 |
|---|---|---|
| `GET /console/versions?fs=` | — | `{featureset, active, versions:[{version, bucket, ready, gate_passed, bholdout_util, has_mlflow}]}` |
| `GET /console/versions/{version}?fs=` | — | `{version, bucket, ready, gate{no_regression·bholdout_util·new_aval_util·old_aval_util·...·eps·cross_site_claim·validated_at}, retrain{epochs·val_loss·b_split_seed·n_*·run_id·git_commit}, meta{featureset·tau·trained_on}, mlflow_link}` |
| `GET /console/audit?event_type=&gate_passed=&since=&fs=` | — | `[{id·ts·event_type·featureset·gate_passed·from_version·to_version·run_id·git_commit·actor_unverified·verified_subject·reason}]` |
| `POST /console/approve` | `{fs, version, actor, reason}` | `{event_id, prev, active, propagation}` |
| `POST /console/rollback` | `{fs, version, actor, reason}` | `{event_id, prev, active, propagation}` |

- **에러 코드**: 422 = `ValueError`/`FileNotFoundError`(미완성·교차-fs·REGRESSED), 403 = `PermissionError`(미승인). 프론트는 이 코드로 분기.
- **`propagation`** = `"confirmed"` | `"pending"`. 서빙 메모리 반영 확인 결과(결정 2-A).
- **`bucket`** = `champion`|`challenger`|`archived`|`incomplete`. `version`은 `gru_<fs>@` 접두 제거된 순수 버전.
- 서빙 메트릭(`serve/metrics.py`): `serve_predict_requests_total`·`serve_predict_latency_seconds`(Histogram)·`serve_alarms_total`·`serve_health_requests_total`·`serve_pred_prob`·`serve_input_feature_value`·`serve_input_missing_total`. (에러 카운터 **없음** — G3 한계로 후술.)
- 기존 Grafana: `deploy/grafana/dashboards/drift.json`(드리프트 6패널) + `provisioning/{datasources/prometheus.yaml, dashboards/drift.yaml}`. 신규 대시보드는 같은 provisioning 메커니즘에 얹음.

---

## 구현 1: React 통합 콘솔 (Vite) — `console-web/`

결정 1(와이어프레임)·결정 3(게이트 수치 크게). 스택 = **Vite + React + TypeScript**, 상태는 React 훅(useState/useEffect)만(무거운 상태 라이브러리 불필요 — 단일 화면), HTTP는 `fetch`. API base = **상대경로 `/console/*`**(Ingress 동일출처, 구현 3) — `VITE_API_BASE` 환경변수, 기본 `""`(동일출처).

### 컴포넌트 구조 (와이어프레임 매핑)

```
App
 ├─ StatusBar              상단 활성(champion) 상태바: active 버전 + 전파상태 배지
 ├─ VersionList            list_versions.versions를 버킷 정렬: champion→challenger→archived→incomplete
 │   └─ VersionRow         접힌 행: version·bucket 배지·gate_passed 신호·bholdout_util 헤드라인·MLflow 아이콘(has_mlflow)
 │       └─ VersionDetail  행 클릭 시 펼침 — get_version_detail 호출(lazy)
 │           ├─ GatePanel    게이트 수치 크게 + 판정 보조(결정 3): util 3종 大, PASS/REGRESSED 배지 小
 │           ├─ RetrainPanel epochs·val_loss·seed·n_*·git_commit
 │           ├─ Actions      승인 / 롤백 버튼(+ ConfirmDialog)
 │           ├─ MlflowLink   mlflow_link 있으면 링크, 없으면 폴백 안내(6-A)
 │           └─ AuditTrail   이 버전 관련 감사(/console/audit?fs= 필터)
 └─ ConfirmDialog          actor 입력 + reason → POST approve/rollback → propagation 결과 표시
```

### 프론트 고유 계약 (백엔드가 강제하지만 UI도 반영해야 하는 것)

1. **REGRESSED 승인 버튼 비활성 (M3 이중 게이트)**: `gate_passed === false`면 승인 버튼 `disabled` + 툴팁("게이트 미통과 — 승인 불가"). 백엔드도 422로 막지만(이중), UI에서 먼저 차단해 헛클릭 방지.
2. **전파 대기/실패 구분 표시 (결정 2-A-c)**: approve/rollback 응답 `propagation`이 `"confirmed"`가 아니면, 그 버전을 "활성(전파 확인됨)"이 아니라 **"전파 대기/실패"** 로 시각 구분(예: 주황 배지). 숨기지 않고 노출. 재폴링 = `GET /console/versions` 재호출로 active 재확인 제공.
3. **1일차 archived 비어있음 명시 (mn1)**: archived 버킷이 비면 "콘솔 도입 이전 이력 없음 — 거짓 복원하지 않음" 안내. 빈 목록을 오류로 보이지 않게.
4. **cross-site 정직성 (코드 강제와 정합)**: GatePanel에 `cross_site_claim`을 표시 — `false`면 "in-distribution 검증 (cross-site 일반화 주장 아님)" 명시. 게이트 수치를 과대해석하지 않게.
5. **버전 식별자 = 디렉토리명 (B1)**: 모든 쓰기 요청의 `version`은 `list_versions`가 준 순수 버전 문자열 그대로 전달(접두 재부착·가공 금지).

### API 클라이언트 (`console-web/src/api.ts`)

```ts
const BASE = import.meta.env.VITE_API_BASE ?? "";
export const getVersions   = (fs: string) => fetch(`${BASE}/console/versions?fs=${fs}`).then(j);
export const getDetail     = (fs: string, v: string) => fetch(`${BASE}/console/versions/${v}?fs=${fs}`).then(j);
export const getAudit      = (q: AuditQuery) => fetch(`${BASE}/console/audit?${qs(q)}`).then(j);
export const approve       = (b: WriteReq) => post(`${BASE}/console/approve`, b);   // 422/403 분기
export const rollback      = (b: WriteReq) => post(`${BASE}/console/rollback`, b);
// post(): !res.ok면 res.status로 분기 — 422=게이트/미완성 메시지, 403=미승인 메시지 표면화
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

### 각 조각이 필요로 하는 것 (직접 작성 시 체크리스트)

- **console-api Deployment**: image=콘솔 이미지(`api:app` uvicorn), port 8000, replicas **1**(직렬화 경계 전제 — 결정 7-1, serve의 "replicas=1 REQUIRED" 주석 패턴 따름). 환경:
  - `ARTIFACTS_DIR` → **serve와 같은 번들 저장소 공유**(alias가 조정점이라 동일 PVC를 양쪽이 마운트 — 핵심 의존). minikube는 hostPath/PVC, 정확한 StorageClass는 `[검증 필요]`.
  - `SERVE_URL=http://sepsis-serving:8000` → `_propagate_and_confirm`이 `/admin/reload`·`/health`를 서빙 Service DNS로 호출(핸드오프 A의 전파 확인이 이 주소를 씀).
  - 감사 DB 영속(`console_audit.db` 또는 PVC). readiness/liveness probe = 가벼운 GET(예: `/console/versions?fs=vitals`, 단 빈 클러스터 200 보장 확인).
- **console-web Deployment**: image=nginx + `console-web` 빌드 산출물(`vite build` → `dist/`를 nginx 정적 루트로). replicas 1(정적이라 stateless, 필요시 증설). nginx는 SPA 폴백(`try_files ... /index.html`)만.
- **Service 2개**: 각 Deployment 앞 ClusterIP(이름 = `console-web`·`console-api`, serve의 `service.yaml` 패턴).
- **Ingress 1개 (새로 배우는 것)**: host `console.<host>`, 2 path rule(`/`→console-web, `/console`→console-api). minikube는 `minikube addons enable ingress`로 nginx-ingress 활성 필요 — `[검증 필요]`(환경 의존).

### 공유 저장소 의존 (가장 중요, 못 박음)

`console-api`와 `serve`는 **같은 `deploy/artifacts`(alias 심링크)를 봐야** 콘솔 swap이 서빙에 반영된다. 별도 볼륨이면 콘솔이 바꾼 alias를 서빙이 못 본다 → 전파 사슬 단절. 따라서 **동일 PVC를 양쪽 Deployment가 마운트**(ReadWriteMany 또는 단일노드 hostPath). 이건 결정 2의 "공유 자원 = 번들 저장소 + 감사 DB"의 K8s 구현. `[검증 필요]`: minikube 단일노드는 hostPath로 족하나, 멀티노드는 RWX PVC 필요.

---

## 성공 기준 (구현 검증 대상)

1. **콘솔 렌더**: `list_versions` 응답으로 StatusBar(active) + 버킷 정렬 리스트가 그려진다. 행 클릭 시 `get_version_detail` lazy 호출로 게이트 수치 크게·판정 보조(결정 3)가 펼쳐진다.
2. **REGRESSED 차단(M3)**: `gate_passed=false` 버전은 승인 버튼 비활성 + 백엔드 422 메시지 표면화(이중 게이트).
3. **전파 표시(2-A-c)**: approve/rollback 후 `propagation!="confirmed"`면 "전파 대기/실패"로 구분 표시, 재폴링 제공. `"confirmed"`면 활성 확정 표시.
4. **정직성 UI**: archived 빔 → "이전 이력 없음(거짓 복원 안 함)" 안내(mn1). GatePanel에 `cross_site_claim=false` 시 "cross-site 주장 아님" 명시.
5. **버전 식별자(B1)**: 쓰기 요청 `version`이 `list_versions`가 준 문자열 그대로(접두 가공 없음).
6. **G3/G4 대시보드**: 신규 `serving_slo.json`이 provisioning으로 자동 로드되고, 지연 p95/p99·처리량·알람률(G3)·up/생존신호(G4)가 실제 메트릭에 걸린다. 에러율 공백이 명시된다.
7. **K8s 라우팅**: Ingress가 `/`→web, `/console`→api로 라우팅해 CORS 없이 동작. console-api·serve가 **같은 artifacts PVC**를 공유해 콘솔 swap이 서빙에 전파된다. console-api replicas=1.
8. **분리 불변**: 콘솔(web/api)이 추론 서빙의 환자별 상태·예측 경로를 건드리지 않는다(결정 2).

## 범위 외 (명시)

- `serve_predict_errors_total`·`serve_build_info` 추가 → 서빙 코드 변경, 권고만(G3 에러율·G4 run_id 패널 원할 시).
- 정확한 PVC StorageClass·minikube ingress addon·`up{job=}` 라벨 → 배포환경 종속 `[검증 필요]`.
- 인증/SSO·콘솔 접근제어 → 후속(핸드오프 A M4와 동일 라인).
- React E2E 테스트 자동화 → 본 단계는 와이어프레임·계약 일치로 검증, 자동 E2E는 후속.
- K8s YAML 실제 파일 → 사용자 직접 작성(CKA). 본 핸드오프는 골격·라우팅·이유.