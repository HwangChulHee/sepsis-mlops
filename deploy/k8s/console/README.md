# 운영 콘솔 K8s 매니페스트 (핸드오프 B 구현 3)

갈래 ㄱ(Ingress 동일 출처) 토폴로지. 3 Deployment(serve / console-api / console-web) +
Ingress + 공유 PVC.

```
[Ingress: console.local]
   ├─ /          -> Service sepsis-console-web -> console-web (nginx + React dist)
   └─ /console/* -> Service sepsis-console-api -> console-api (FastAPI)
serving(sepsis-serving)은 기존 deploy/k8s/ 그대로. 단, 공유 PVC 마운트 추가 필요(아래).
```

## 파일

| 파일 | 내용 |
|---|---|
| `artifacts-pvc.yaml` | 공유 번들 저장소 PVC(`sepsis-artifacts`, B2) + 감사 DB 영속 PVC(`sepsis-console-audit`) |
| `console-api.yaml` | console-api Deployment(replicas=1, Recreate, 비-root) + Service. 감사 DB PVC 마운트(`/app/auditdb`) + `CONSOLE_AUDIT_DB_URL` |
| `console-web.yaml` | console-web Deployment(replicas=2, 비-root nginx 8080) + nginx ConfigMap + Service + PodDisruptionBudget |
| `networkpolicy.yaml` | default-deny + 명시 허용(ingress-nginx→web/api, api→serving:8000, DNS) |
| `ingress.yaml` | 경로 라우팅(/ → web, /console → api) |

## 적용 순서

```bash
# 0) (minikube) ingress 컨트롤러 활성
minikube addons enable ingress

# 1) PVC 먼저 (공유 번들 + 감사 DB 영속 — 한 파일에 둘 다)
kubectl apply -f deploy/k8s/console/artifacts-pvc.yaml

# 2) 앱
kubectl apply -f deploy/k8s/console/console-api.yaml
kubectl apply -f deploy/k8s/console/console-web.yaml

# 3) 네트워크 격리
kubectl apply -f deploy/k8s/console/networkpolicy.yaml

# 4) 라우팅
kubectl apply -f deploy/k8s/console/ingress.yaml

# 5) (host 매핑) minikube ip -> /etc/hosts 에 'console.local' 추가
```

## 선행: serving도 같은 PVC를 마운트 (B2 — 적용 완료)

`deploy/k8s/deployment.yaml`(serving)에 공유 PVC(`sepsis-artifacts`)를
`/app/deploy/artifacts` 로 마운트하는 설정이 **이미 포함**되어 있다. 콘솔이 PVC 의 alias 를
바꾸면 serving 이 같은 경로에서 그 alias 를 읽어 전파 사슬이 닫힌다. (이미지에는 더 이상
artifacts 를 굽지 않는다 — `deploy/Dockerfile` 은 빈 디렉토리만 만들고 PVC 가 런타임에 덮음.)

> 참고: serving 의 featureset 선택은 `configmap.yaml` 의 `SERVE_FEATURESET`(기본 `vitals`)로
> 한다. 레거시 `RUN`/`SERVE_BUNDLE_DIR` env 는 alias 통일(mn-B)로 제거됐다 — `app.py` 는
> 활성 alias `gru_<fs>` 를 ARTIFACTS 아래에서 직접 해석한다.

## [검증 필요] (환경 종속 — 운영 배포 시점 확정)

- **감사 DB 영속**: console-api 는 `CONSOLE_AUDIT_DB_URL=sqlite:////app/auditdb/console_audit.db`
  로 감사 이력을 영속 PVC(`sepsis-console-audit`, RWO 1Gi)에 쓴다. 이게 빠지면(ephemeral FS)
  파드 재시작마다 승인/롤백 이력이 사라진다. **[검증 필요]** 운영에선 sqlite 단일파일 대신
  외부 DB(Postgres 등)로 승격 권장 — 단일 replica·RWO 전제라 현재는 sqlite 로 충분.
- **[M4 빚 — 인증]** Ingress 가 `/console` 쓰기(approve·rollback)를 **무인증** 노출한다.
  운영 전 인증 필수. 최소 방어는 `ingress.yaml` 의 basic-auth annotation 주석 참고, 전면
  인증(SSO/OIDC·역할기반)은 M4 범위.
- **PVC accessMode**: 단일노드 minikube는 RWO로 충분. 멀티노드 → RWX + RWX StorageClass.
- **이미지 2개**: `sepsis-console:a`(uvicorn console.api:app), `sepsis-console-web:a`(nginx+dist).
  Dockerfile은 본 매니페스트 범위 밖.
- **Ingress host/class**: `console.local`·`ingressClassName: nginx`는 예시. 실제 환경에 맞게.
- **console-api readiness**: `/console/versions?fs=vitals`가 빈 클러스터(번들 0개)에서도 200인지 확인.
- **`up{job=}`(Grafana G4)**: Prometheus가 serving을 긁는 job 이름과 일치시킬 것.
- **SERVE_URL**: `http://sepsis-serving`(serving Service, port 80) — 전파 확인 대상. namespace
  다르면 FQDN(`sepsis-serving.<ns>.svc`)로.
