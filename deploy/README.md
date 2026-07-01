# 배포 런북 — sepsis-serving + 운영 콘솔 (minikube/K8s)

로컬 minikube에 **서빙 + 운영 콘솔 풀스택**을 처음부터 띄우는 순서. 콘솔 매니페스트 세부는
[`deploy/k8s/console/README.md`](k8s/console/README.md) 참고 — 이 문서는 **전체 흐름 + 번들 시딩 +
알려진 함정**을 다룬다.

> 표기: `[확인됨]` = 이 리포에서 실제 실행·측정으로 검증. `[우리 결정]` = 운영 판단.

---

## 0. 무엇이 뜨는가

| 구성요소 | 이미지 | 매니페스트 | replicas |
|---|---|---|---|
| serving (FastAPI `/predict`) | `sepsis-serving:h4s` (~1.48GB, torch) | `k8s/deployment.yaml`·`service.yaml`·`configmap.yaml` | 1 (in-memory 환자 state → 단일) |
| console-api (승인·롤백·감사) | `sepsis-console:a` (~175MB, alpine) | `k8s/console/console-api.yaml` | 1 |
| console-web (React+nginx) | `sepsis-console-web:a` (~81MB) | `k8s/console/console-web.yaml` | 2 |
| 공유 번들 PVC | — | `k8s/console/artifacts-pvc.yaml` (`sepsis-artifacts` 2Gi, `sepsis-console-audit` 1Gi) | — |

핵심 배선: console-api와 serving이 **같은 PVC(`sepsis-artifacts`)를 같은 경로
`/app/deploy/artifacts`** 에 마운트한다. 콘솔이 alias(`gru_<fs>` 심링크)를 바꾸면 serving이
같은 경로에서 그 버전을 읽어 전파가 성립한다(B2). serving은 MLflow가 아니라 **이 PVC의
번들 디렉토리**를 읽는다.

---

## 1. 사전 요건

```bash
minikube start --driver=docker      # 노드 CPU 넉넉하면 좋다(캘리브레이션 부팅비용, §7 참고)
minikube addons enable ingress      # 콘솔 Ingress 쓸 때만
```

## 2. 이미지 빌드 (호스트 docker)

```bash
# 서빙 (torch CPU wheel 포함 — 수 분)
docker build -t sepsis-serving:h4s -f deploy/Dockerfile .
# 콘솔 API (alpine, torch 없음)
docker build -t sepsis-console:a -f deploy/k8s/console/Dockerfile.api .
# 콘솔 웹 (node build → nginx-unprivileged)
docker build -t sepsis-console-web:a -f console-web/Dockerfile console-web
```

## 3. ★ 이미지를 minikube 안으로 로드 (docker 드라이버 함정)

`[확인됨]` minikube는 **자체 내부 docker 데몬**을 쓴다. 호스트에 빌드한 이미지는 그대로는 안
보이고, 매니페스트가 `imagePullPolicy: IfNotPresent` + 레지스트리 없음이라 로드 안 하면
**`ErrImageNeverPull`/`ImagePullBackOff`** 가 난다.

```bash
minikube image load sepsis-serving:h4s        # 1.48GB — 느림(1회성)
minikube image load sepsis-console:a sepsis-console-web:a
minikube image ls | grep sepsis               # 3개 다 보이는지 확인
```
> 대안: `eval $(minikube docker-env)` 후 §2를 그대로 빌드하면 로드 단계 생략(내부 데몬에 직접 굽는다).

## 4. 매니페스트 적용

```bash
# (1) PVC 먼저 — 공유 번들 + 감사 DB
kubectl apply -f deploy/k8s/console/artifacts-pvc.yaml
# (2) 서빙
kubectl apply -f deploy/k8s/configmap.yaml -f deploy/k8s/service.yaml -f deploy/k8s/deployment.yaml
# (3) 콘솔 (세부: k8s/console/README.md)
kubectl apply -f deploy/k8s/console/console-api.yaml -f deploy/k8s/console/console-web.yaml
kubectl apply -f deploy/k8s/console/networkpolicy.yaml -f deploy/k8s/console/ingress.yaml
```

## 5. ★★ 번들을 PVC에 시딩 (헤매기 쉬운 핵심)

갓 만든 PVC는 **비어 있다** — 이미지에 번들을 굽지 않는다(Dockerfile은 빈 디렉토리만 만들고
PVC가 런타임에 덮음). serving이 `/health`(→ 번들 로드)를 통과하려면 PVC에 유효한 번들 버전
디렉토리 + `gru_<fs>` alias가 있어야 한다.

**번들 version dir 1개 = 파일 4개**: `meta.json` · `model.pt` · `pre.npz` · `reference.npz`.
(`.ready`·`retrain.json`·`validation.json`은 콘솔 메타일 뿐 **서빙 로더는 안 본다** — `[확인됨]`.)

실학습 GRU(호스트 `deploy/artifacts/gru_vitals@v0-base`, run `8de08ed…`)를 앉히는 예:

```bash
POD=$(kubectl get pod -l app=sepsis-serving -o jsonpath='{.items[0].metadata.name}')
# (a) version dir 복사
kubectl cp deploy/artifacts/gru_vitals@v0-base "$POD:/app/deploy/artifacts/gru_vitals@v0-base"
# (b) 활성 alias 를 그 버전으로 (원자적 ln -sfn)
kubectl exec "$POD" -- ln -sfn gru_vitals@v0-base /app/deploy/artifacts/gru_vitals
```

**리로드 방법 2가지:**
- 무중단: `curl -X POST http://<serving>/admin/reload` (alias 재해석 → 새 버전 로드).
- 확실히: `kubectl rollout restart deployment/sepsis-serving` (부팅 시 alias를 새로 로드).

> 운영에선 이 시딩·alias 교체를 **콘솔**(승인=alias 스왑+감사)이 한다. 위는 부트스트랩/디버깅용.
> `[확인됨]` PVC 파일 소유 uid는 `fsGroup: 10001` 정렬 — `kubectl cp`/`exec`가 컨테이너 uid(10001)로
> 써서 그대로 읽힌다.

## 6. 검증

```bash
kubectl port-forward svc/sepsis-serving 8000:80 &
curl -s localhost:8000/health      # run_id 가 방금 앉힌 번들인지 확인
# 실제 환자 재생(위험도 곡선) — 매 실행 fresh patient_id(stale state 회피 내장)
uv run python scripts/replay/replay_patient.py \
  --psv data/raw/training_setB/p100013.psv --base-url http://localhost:8000 --speed 100000
```
`[확인됨]` 실학습 GRU + 실제 패혈증 환자: t=0 p≈0.47 → t≈5 알람 발화(τ=0.573 교차) → p≈0.94.

---

## 7. ⚠️ 알려진 함정 — 캘리브레이션 vs startup probe 타임아웃

`[확인됨]` **증상**: 새 파드가 `0/1 Running`에서 안 넘어가고 `Startup probe failed: context
deadline exceeded`가 반복. 5분+ 크래시루프처럼 보인다.

**원인**: 첫 `/health`가 드리프트 캘리브레이션(`DRIFT_CAL_TRIALS` 기본 300 trial)을 `_LOCK`
아래 **동기 수행**한다. 호스트 ~10.7s인데 **CPU limit 1코어 파드에선 BLAS 스로틀로 60s+**.
`startupProbe.timeoutSeconds: 10`을 매번 넘겨 무한 재시도. (이 캘리브레이션은 `/drift`
엔드포인트 전용 — **`/predict` 위험도 곡선과는 무관**하다.)

**즉효 완화(라이브 패치, 매니페스트엔 없음 → 재생성 시 회귀 주의):**
```bash
kubectl patch deployment sepsis-serving --type=json -p '[
  {"op":"add","path":"/spec/template/spec/containers/0/env/-","value":{"name":"DRIFT_CAL_TRIALS","value":"30"}},
  {"op":"replace","path":"/spec/template/spec/containers/0/resources/limits/cpu","value":"2"}
]'
```
`DRIFT_CAL_TRIALS=30`(≈1s 호스트) + CPU 여유면 한 번에 부팅된다. 노드 CPU가 넉넉하면 limit
상향이 특히 잘 듣는다.

**항구적 조치(`[우리 결정]` — 매니페스트 반영 필요, 방향 택1):**
1. `startupProbe`의 `timeoutSeconds`·`failureThreshold` 상향 (probe만 손봄, 캘리 품질 유지).
2. 부팅 캘리브레이션을 백그라운드/lazy 로 분리 (구조 변경, readiness 정의 재검토).
3. CPU request/limit 상향 (스로틀 완화).
> 설계 변경이므로 `.claude` review-loop(redteam)을 태워 반영 권장.

---

## 8. 정리

```bash
minikube stop          # 삭제 아님 — 상태 보존, 다음에 그대로 재사용
# 완전 제거: minikube delete
```

## 참고
- 서빙 설계/결정: [`docs/design/h4/serving/decisions.md`](../docs/design/h4/serving/decisions.md)
- 콘솔 매니페스트: [`deploy/k8s/console/README.md`](k8s/console/README.md)
- 스케일아웃 제약(단일 replica 이유): [`docs/adr/serving-scaling.md`](../docs/adr/serving-scaling.md)
