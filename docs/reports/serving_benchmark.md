# Serving Benchmark — GRU vs XGBoost (실측, 강화판)

> uvicorn 서브프로세스(순차) + 실제 setB 환자 스트림. 워밍업 15 제외, 측정창 100×5회 반복. 전처리 포함 경계(GRU StreamPreprocessor / XGB 버퍼→lookback_summary 재구성).

> **정직성**: 헤드라인=(아키텍처×featureset) 결합 배포 프로파일. client−server 잔차는 arm-1 에서 "network"라 부르지 않음. best_iter 절단·골든은 uv.lock(xgboost 3.3.0) 전제.

## 1. server 내부 추론 latency + 아키텍처/featureset 분리 (ms, 중앙값[min–max])

| featureset arm | server_mean 중앙값 | 반복 spread |
|---|---|---|
| GRU / vitals9 | 0.564 | [0.554–0.610] |
| XGB / vitals9 (통제) | 2.294 | [2.279–2.328] |
| XGB / vitals_labs18 (배포) | 2.280 | [2.253–2.326] |

- **아키텍처 기여**(featureset=9 고정, XGB/9 − GRU/9) = **+1.730 ms**.
- **featureset 기여**(XGB 9→18) = **-0.013 ms**.
- 통제 arm(XGB/9)을 latency 로도 재서, 배포 arm(XGB/18)의 재구성 비용 중 아키텍처 몫과 featureset(입력차원 2배) 몫을 분리. GRU 는 hidden state 로 O(1), XGB 는 매 요청 8행 버퍼 lookback 재구성 + 트리 절단이라 무겁다.

## 2. 헤드라인 — 배포 arm latency (client/server, ms)

| 배포 arm | client 벽시계(p50/95/99) | server(p50/95/99) | client_mean | server_mean | residual | tax |
|---|---|---|---|---|---|---|
| GRU/vitals9 | 2.13/3.12/7.20 | 0.55/0.70/0.86 | 2.358 | 0.571 | 1.787 | -0.061 |
| XGB/vitals_labs18 | 3.65/8.34/9.60 | 2.23/2.70/2.98 | 4.039 | 2.283 | 1.756 | 0.102 |

- residual=client_mean−server_mean(버킷 무관, label='client_server_residual', arm-1 network 아님). tax=arm1−arm2 잔차(부가계측 세금). arm-2 network 추정: GRU 1.847/XGB 1.654 ms.

## 3. throughput (동시 부하)
| 모델 | n_streams | req/sec | wall(s) |
|---|---|---|---|
| GRU/vitals9 | 4 | 679.3 | 0.18 |
| XGB/vitals_labs18 | 4 | 434.8 | 0.28 |

## 4. 메모리 — peak RSS + 상태 메모리 sweep

| 모델 | RSS(arm1,MB) | peak | 계측 세금(arm1−arm2) | featureset(control9−arm1) |
|---|---|---|---|---|
| GRU/vitals9 | 262 | 262 | 12.3 | 0.0 |
| XGB/vitals_labs18 | 237 | 237 | 10.4 | -8.7 |

- **상태 메모리 기울기(환자 수 sweep [0, 1000, 3000, 6000])**: GRU **+1.823 MB/1k환자**, XGB **+1.476 MB/1k환자**. (GRU=hidden state, XGB=8행 버퍼 — 둘 다 환자 수에 증가하나 환자당 sub-KB 라 수천 명 규모에선 RSS 노이즈 근처. **XGB stateless 아님**을 기울기로 확인.)
- 헤드라인 RSS 차(GRU 262 vs XGB 237)는 주로 torch vs xgboost 런타임 footprint. 계측 세금·입력차원 기여는 이 규모에서 노이즈 수준.

## 5. 비용 환산 (수동)

- 목표 1000 req/s, 측정 per-instance 434.8 req/s → 3대 × $0.17/hr = **$0.51/hr**. 인스턴스 c6i.xlarge (vCPU4/8GiB, 예시), 출처 https://aws.amazon.com/ec2/pricing/on-demand/ (2026-07 조회 예시).

## 6. 공정성·게이트·노이즈 관측

- 정상상태 컷 index GRU=2/XGB=2(−1=비수렴 FAIL). 부팅 분리 GRU 1.30s/XGB 1.26s.
- 게이트(A1): arm-1 피처라인 GRU=9/XGB=16(>0), arm-2 GRU=0/XGB=0(==0).
- 노이즈: server_mean 반복 spread(위 §1). tax·계측세금·입력차원 기여가 spread 안에 들면 노이즈로 해석.

## 7. 한계 (정직)

- client 벽시계=localhost httpx(실 network 작음) — 잔차 network 성분은 arm-2 에서만 추정, 여기선 직렬화+프레임워크가 주.
- 단일 머신·소표본. 반복 5회로 노이즈는 줄였으나 절대치는 하드웨어 의존. best_iter 골든=xgboost 3.3.0 전제.
- 상태 sweep 은 합성 pid(고정 행)로 상태 엔트리만 적재 — 환자당 메모리는 sub-KB라 6000명에서도 소량. 대규모(10만+)는 별도.
- throughput 은 소규모 동시 스트림(스모크). 대규모 부하·네트워크 배포는 별도.
