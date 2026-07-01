# Serving Benchmark — GRU vs XGBoost (실측)

> 실측: uvicorn 서브프로세스(순차, 자원경합 배제) + 실제 환자 PSV 스트림(setB). 워밍업 제외, 전처리 포함 경계(GRU StreamPreprocessor / XGB 버퍼→lookback_summary 재구성).

> **정직성**: 헤드라인은 (아키텍처 × featureset) **결합 배포 프로파일** — GRU/vitals9 vs XGB/vitals_labs18. 순수 아키텍처 운영비가 아니다. client−server 잔차는 arm-1 에서 "network"라 부르지 않는다(network+직렬화+핸들러 후처리). best_iter 절단·골든은 uv.lock(xgboost 3.3.0) 전제.

## 1. 헤드라인 — latency (정상상태, ms, p50/p95/p99)

| 모델(배포) | client 벽시계 | server 내부 | client_mean | server_mean | residual(mean) | tax(계측세금) |
|---|---|---|---|---|---|---|
| GRU/vitals9 | 2.14/2.86/8.13 | 0.55/0.72/0.95 | 2.331 | 0.577 | 1.754 | -0.119 |
| XGB/vitals_labs18 | 3.63/4.67/9.15 | 2.22/2.70/2.89 | 3.890 | 2.265 | 1.625 | -0.001 |

- `residual = client_mean − server_mean` (버킷 무관 평균, 동일 정상상태 슬라이스). `residual_label`='client_server_residual' — arm-1 에서 network 아님.
- `tax = arm1.residual − arm2.residual` = 부가 계측(피처 히스토그램+drift 윈도우) 세금. arm-2(순수추론) network 추정: GRU 1.872 / XGB 1.626 ms (label='network_plus_serialization').

## 2. throughput (동시 부하)

| 모델 | n_streams | req/sec | wall(s) |
|---|---|---|---|
| GRU/vitals9 | 4 | 549.4 | 0.22 |
| XGB/vitals_labs18 | 4 | 442.4 | 0.27 |

## 3. 메모리 (peak RSS, MB) + 3기여 분해

| 모델 | RSS(arm1) | peak | 계측 부속물(arm1−arm2) | 입력차원(control9−arm1) | state |
|---|---|---|---|---|---|
| GRU/vitals9 | 250 | 250 | 1 | 0 | (환자수 sweep, presence) |
| XGB/vitals_labs18 | 228 | 228 | 1 | -2 | (presence) |

- **XGB 도 stateless 아님**(`stateless_claim=False`) — 환자별 8행 lookback 버퍼 = per-patient 상태. 메모리 차이를 아키텍처로 뭉뚱그리지 않고 3기여로 분해.
- featureset 기여(memory.rss, XGB 9→18) = 2 MB (= −input_dim). 통제 arm XGB/vitals9 RSS = 226 MB.

## 4. 비용 환산 (수동)

- 목표 throughput 1000 req/s, 측정 per-instance 442.4 req/s → 인스턴스 3대 × $0.17/hr = **$0.51/hr**.
- 인스턴스: c6i.xlarge (vCPU4/8GiB, us-east-1 예시). 요금 출처: https://aws.amazon.com/ec2/pricing/on-demand/ (2026-07 조회 예시).
- (per-instance = XGB/vitals_labs arm-1 측정 req/s. GRU/XGB 비용 대비는 각 req/s 로 환산.)

## 5. 공정성·정상상태·게이트 관측

- 워밍업 15 요청 제외, 측정 창 120 요청. 정상상태 컷 index: GRU=2, XGB=2 (−1이면 비수렴 FAIL).
- 부팅 비용(모델 로드+캘리브레이션) 분리: GRU boot=1.31s, XGB boot=1.27s.
- 관측성 게이트 확인(A1): arm-1 피처 샘플라인 GRU=9/XGB=16 (>0), arm-2 GRU=0/XGB=0 (==0 이면 게이트 동작).

## 6. 한계 (정직)

- client 벽시계는 localhost httpx — 실 네트워크 왕복은 작다. 잔차의 network 성분은 arm-2 에서만 추정하며 여기선 직렬화+프레임워크가 주. 데이터센터 배포 network 는 별도.
- server 요청별 latency 는 `_sum` 인접 델타(단일 스트림 순차라 깨끗). 분위수는 분포 참고(버킷 무관 평균이 load-bearing).
- 단일 실행·소표본(1 실행). CI/다른 하드웨어에서 절대치 다름. best_iter 골든은 xgboost 3.3.0 전제.
- throughput 은 소규모 동시 스트림(스모크 수준). 대규모 부하는 별도.
