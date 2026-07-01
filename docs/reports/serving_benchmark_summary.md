# Serving Benchmark 요약 — GRU vs XGBoost (로컬 ↔ k8s)

> 두 실측(로컬 localhost / k8s 인-클러스터)을 한 장으로 묶은 요약. 상세는 각 리포트 참조:
> [`serving_benchmark.md`](serving_benchmark.md)(로컬 강화판) · [`serving_benchmark_k8s.md`](serving_benchmark_k8s.md)(k8s).
> 원시 로컬 측정값: [`serving_benchmark_measurements.json`](serving_benchmark_measurements.json).

## 무엇을 쟀나

ICU 패혈증 예측 두 모델을 **서빙(라이브 서버)** 으로 띄워 "정확도가 아니라 **운영비**"를 비교했다.
- **GRU**(신경망): 환자별 hidden state를 들고 timestep당 **O(1)** 전진.
- **XGBoost**(트리): 매 요청마다 **8행 lookback 버퍼 → 요약통계 재구성 + best_iter 절단 트리** 평가.

측정 축: server 내부 추론 latency, client 벽시계(네트워크 포함), throughput, 메모리(peak RSS),
관측성 게이트(arm-1 계측 ON / arm-2 OFF). 헤드라인은 **(아키텍처 × featureset) 결합 배포 프로파일**
(GRU/vitals9 vs XGB/vitals_labs18) — 순수 아키텍처 운영비가 아니며, 통제 arm(XGB/vitals9)으로
두 기여를 분리한다.

---

## 로컬 ↔ k8s 비교 (핵심)

### server 내부 추론 latency (ms, 중앙값)

| featureset arm | 로컬(스레드 무제한) | k8s(cgroup, 스레드 캡=1) |
|---|---|---|
| GRU / vitals9 | 0.564 | 0.383 |
| XGB / vitals9 (통제) | 2.294 | 0.658 |
| XGB / vitals_labs18 (배포) | 2.280 | 0.769 |
| **아키텍처 기여**(XGB9−GRU9) | **+1.730** | **+0.275** |
| **featureset 기여**(XGB 18−9) | −0.013 (노이즈) | +0.112 |

### 배포 arm — client(네트워크 포함) / 메모리

| | 로컬 | k8s |
|---|---|---|
| GRU client_mean / server_mean | 2.358 / 0.571 | 1.557 / 0.378 |
| XGB client_mean / server_mean | 4.039 / 2.283 | 2.073 / 0.765 |
| **residual**(client−server, GRU/XGB) | 1.79 / 1.76 ms | 1.18 / 1.31 ms |
| GRU pod/proc RSS | ~262 MB | **248 MB (torch)** |
| XGB pod/proc RSS | ~237 MB | **111 MB (torch 미사용)** |
| throughput GRU / XGB (req/s) | 679 / 435 | 664 / 475 |
| 게이트(arm1 피처라인 / arm2) | 9·16 / 0 | 9·18 / 0 |

---

## 핵심 해석 (3가지)

### 1. "XGB 4배 느림"은 상당 부분 **스레드 설정 아티팩트**였다
로컬에선 XGB server latency가 GRU의 ~4배(2.29 vs 0.56ms), 아키텍처 기여 +1.73ms로 나왔다.
그러나 이는 **BLAS 스레드 무제한** 탓이 크다 — XGB의 8×F 작은 배열 재구성에 스레드가 과다
투입돼 **경합(thrash)** 이 났다. **k8s에서 CPU를 1코어로 제한하고 스레드도 1로 캡**(프로덕션 정석)
하니 경합이 사라져, 아키텍처 기여가 **+0.28ms(~2배)** 로 줄었다.
→ **결론: 컨테이너 자원/스레드 설정이 벤치 결과를 바꾼다. k8s(스레드 캡) 수치가 프로덕션에 더 충실.**
여전히 XGB가 GRU보다 무겁다(재구성+트리 vs O(1))는 방향은 두 실측 공통.

### 2. GRU 서빙은 **메모리가 2배 이상** — torch 런타임 때문
k8s 컨테이너 RSS로 깨끗이 갈렸다: **GRU 248MB vs XGB 111MB.** XGB 서빙 이미지는 **torch를
싣지 않아** 절반 이하. 헤드라인 메모리 차는 아키텍처 알고리즘이 아니라 **런타임 footprint**가 주다.

### 3. **XGB는 stateless가 아니다** (설계 검토 발견을 실측 확인)
환자 수 sweep(로컬, 0→6000명)에서 GRU **+1.82**, XGB **+1.48 MB/1k환자** — 둘 다 환자 수에 따라
메모리 증가. XGB도 환자별 8행 버퍼라는 per-patient 상태를 가진다. 설계 루프에서 "XGB stateless"가
거짓으로 판정된 것(NB2)이 라이브 기울기로 뒷받침됐다.

### 부수 관측
- **네트워크 잔차**(residual, client−server): 로컬 ~1.7ms(직렬화·프레임워크 위주) vs k8s ~1.2ms.
  arm-1 잔차를 "network"라 부르지 않는다(network+직렬화+핸들러 후처리 혼재). arm-2(순수추론)에서만
  network 추정.
- **계측 세금(arm-1 vs arm-2)·메모리 3기여 분해**: 이 규모(소표본·MB 스냅샷)에선 노이즈 수준.
  뚜렷한 신호는 server latency 아키텍처 gap과 메모리 런타임 차뿐.
- **관측성 게이트**: 로컬·k8s 파드 양쪽에서 동작(arm-1 피처 시계열 노출 / arm-2 부재).

---

## 정직한 한계

- **k8s = minikube 단일 노드.** 파드↔파드가 같은 노드 가상 브리지라, 진짜 멀티노드 클러스터
  (노드 간 홉·오버레이 CNI)보다 network가 작다. residual의 network 성분은 그만큼 과소.
- **소표본·단일 실행**(측정창 100×반복 5). 절대치는 하드웨어·설정 의존. 방향(아키텍처/메모리/상태)은 견고.
- **best_iter 골든·XGB 확률 재현은 xgboost 3.3.0(uv.lock) 전제.** 버전 오르면 재산출 필요.
- k8s 실측은 **합성 결정론 스트림**(로컬은 실제 setB 환자 PSV). throughput은 스모크 수준(4 스트림).

---

## 재현

```bash
# 로컬 실측(uvicorn 서브프로세스 + 실제 환자 PSV):
uv run python scripts/bench/run_serving_benchmark.py   # -> serving_benchmark.md

# k8s 실측(minikube 필요):
minikube start --driver=docker --addons=metrics-server
eval $(minikube -p minikube docker-env)
#   아티팩트 스테이징(.dockerignore 우회): deploy/bench/bake/{gru_vitals,xgb_vitals,xgb_vitals_labs}
docker build -f deploy/bench/Dockerfile.bench -t sepsis-bench:latest .
uv run python scripts/bench/run_k8s_benchmark.py       # -> serving_benchmark_k8s.md
uv run python scripts/bench/run_k8s_benchmark.py --cleanup   # 리소스 정리
```

- 벤치 하니스 집계 로직: `src/sepsis/bench/result.py`(테스트 `tests/bench/`).
- 서빙 앱: GRU `src/sepsis/serve/bench_app.py`, XGB `src/sepsis/serve/xgb_app.py`, 관측성 게이트
  `src/sepsis/serve/metrics.py`(`SEPSIS_SERVE_AUX_METRICS`).

## 남은 개선 여지 (백로그)
- 멀티노드 실 클러스터에서 network 잔차 재측정(minikube 단일노드 한계 해소).
- 다회 실행·큰 표본으로 계측 세금·메모리 3기여를 노이즈 밖으로.
- 대규모 동시 부하(수백 스트림) throughput, 환자 10만+ 상태 메모리.
- 트랜스포머(가중치 확보 시) 3모델 확장.
