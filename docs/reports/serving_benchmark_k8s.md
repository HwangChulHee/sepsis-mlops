# Serving Benchmark — GRU vs XGBoost (k8s 실측)

> **k8s(minikube) 인-클러스터 실측**: GRU/XGB 서빙을 파드로 띄우고 **인-클러스터 클라이언트 파드**로 측정. client 벽시계에 **파드↔파드 실 네트워크(ClusterIP·kube-proxy/iptables)** 포함 — 로컬(localhost) 실측과 대비된다. 합성 결정론 스트림, 워밍업 제외, 측정창 100×5회 반복.

> 정직성: 헤드라인=(아키텍처×featureset) 결합 배포 프로파일. arm-1 잔차를 network 라 부르지 않음. best_iter 절단·골든=xgboost 3.3.0. minikube 단일노드라 데이터센터 멀티노드 network 와는 또 다름.

## 1. server 내부 추론 latency + 아키텍처/featureset 분리 (ms, 중앙값[min–max])

| featureset arm | server_mean | 반복 spread |
|---|---|---|
| GRU/vitals9 | 0.383 | [0.345–0.393] |
| XGB/vitals9 (통제) | 0.658 | [0.630–0.822] |
| XGB/vitals_labs18 (배포) | 0.769 | [0.752–0.774] |

- **아키텍처 기여**(XGB/9 − GRU/9) = **+0.275 ms**. **featureset 기여**(XGB 9→18) = **+0.112 ms**.

## 2. 헤드라인 배포 arm — client(파드↔파드 네트워크 포함) / server

| 배포 arm | client(p50/95/99) | server(p50/95/99) | client_mean | server_mean | residual | tax |
|---|---|---|---|---|---|---|
| GRU/vitals9 | 1.48/2.20/2.58 | 0.37/0.55/0.69 | 1.557 | 0.378 | 1.179 | 0.154 |
| XGB/vitals_labs18 | 2.03/2.68/3.15 | 0.76/0.97/1.17 | 2.073 | 0.765 | 1.308 | 0.238 |

- **residual = client_mean − server_mean = k8s 네트워크+직렬화+핸들러 후처리**(label='client_server_residual', arm-1 network 단독 아님). 로컬 localhost 잔차(~1.7ms)와 비교하면 이 값이 k8s 네트워크 오버헤드를 반영.

## 3. throughput (인-클러스터 동시 부하)
| 모델 | n_streams | req/sec | wall(s) |
|---|---|---|---|
| GRU/vitals9 | 4 | 664.3 | 0.18 |
| XGB/vitals_labs18 | 4 | 474.6 | 0.25 |

## 4. 메모리 (파드 peak RSS = VmHWM, MB)
| 모델 | RSS(arm1) | peak | 계측세금(arm1−arm2) | featureset(control9−arm1) |
|---|---|---|---|---|
| GRU/vitals9 | 248 | 251 | 0.4 | 0.0 |
| XGB/vitals_labs18 | 111 | 111 | 0.4 | -0.5 |

- **XGB stateless 아님**(stateless_claim=False). 파드 cgroup 안 RSS 라 컨테이너 기준. 계측세금·입력차원 기여는 이 규모 노이즈 수준(프로세스별 RSS 지터).

## 5. 비용 (수동)

- 목표 1000 req/s, per-instance 474.6 req/s → 3대 × $0.17/hr = **$0.51/hr**.

## 6. 게이트·환경 관측

- 관측성 게이트(A1): arm-1 피처라인 GRU=9/XGB=18(>0), arm-2 GRU=0/XGB=0(==0) — k8s 파드에서도 게이트 동작.
- 정상상태 컷 GRU=2/XGB=2. 파드 리소스 limit cpu=1·mem=900Mi, BLAS 스레드 캡=1.

## 7. 한계 (정직)

- **minikube 단일 노드**: 파드↔파드 네트워크는 같은 노드 내 가상 브리지라, 진짜 멀티노드 클러스터(노드 간 홉·오버레이 CNI)보다 network 가 작다. 그래도 kube-proxy/iptables·서비스 추상은 탄다.
- 합성 스트림·소표본·단일 실행(반복 5회). CPU limit 1 로 캡 — 실제 노드 자원과 다름.
- best_iter 골든=xgboost 3.3.0 전제. throughput 은 스모크 수준(4 스트림).
