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
| `artifacts-pvc.yaml` | 공유 번들 저장소 PVC(console-api ↔ serving alias 공유, B2) |
| `console-api.yaml` | console-api Deployment(replicas=1) + Service |
| `console-web.yaml` | console-web Deployment + nginx ConfigMap + Service |
| `ingress.yaml` | 경로 라우팅(/ → web, /console → api) |

## 적용 순서

```bash
# 0) (minikube) ingress 컨트롤러 활성
minikube addons enable ingress

# 1) 공유 PVC 먼저
kubectl apply -f deploy/k8s/console/artifacts-pvc.yaml

# 2) 앱
kubectl apply -f deploy/k8s/console/console-api.yaml
kubectl apply -f deploy/k8s/console/console-web.yaml

# 3) 라우팅
kubectl apply -f deploy/k8s/console/ingress.yaml

# 4) (host 매핑) minikube ip -> /etc/hosts 에 'console.local' 추가
```

## ⚠️ 선행: serving도 같은 PVC를 마운트해야 함 (B2 — 빠지면 전파 silent 단절)

`deploy/k8s/deployment.yaml`(serving)이 현재 `/app/deploy/artifacts`를 **자체 경로**로 읽는다.
콘솔이 바꾼 alias를 serving이 보려면 **같은 PVC를 동일 경로에 마운트**해야 한다. serving
Deployment에 아래를 추가:

```yaml
# spec.template.spec.containers[0] 에:
          volumeMounts:
            - name: artifacts
              mountPath: /app/deploy/artifacts
# spec.template.spec 에:
      volumes:
        - name: artifacts
          persistentVolumeClaim:
            claimName: sepsis-artifacts
```

이게 빠지면: 콘솔 승인/롤백은 PVC의 alias를 바꾸지만 serving은 이미지에 구운 옛 artifacts를
계속 봐서, `/console` UI는 "전파 대기/실패"로 뜨거나(전파 확인 폴링이 run_id 불일치 감지) 더
나쁘게는 조용히 안 바뀐다. **이 패치를 함께 적용해야 핸드오프 B의 전파 사슬이 닫힌다.**

## [검증 필요] (환경 종속 — 운영 배포 시점 확정)

- **PVC accessMode**: 단일노드 minikube는 RWO로 충분. 멀티노드 → RWX + RWX StorageClass.
- **이미지 2개**: `sepsis-console:a`(uvicorn console.api:app), `sepsis-console-web:a`(nginx+dist).
  Dockerfile은 본 매니페스트 범위 밖.
- **Ingress host/class**: `console.local`·`ingressClassName: nginx`는 예시. 실제 환경에 맞게.
- **console-api readiness**: `/console/versions?fs=vitals`가 빈 클러스터(번들 0개)에서도 200인지 확인.
- **`up{job=}`(Grafana G4)**: Prometheus가 serving을 긁는 job 이름과 일치시킬 것.
- **SERVE_URL**: `http://sepsis-serving`(serving Service, port 80) — 전파 확인 대상. namespace
  다르면 FQDN(`sepsis-serving.<ns>.svc`)로.
