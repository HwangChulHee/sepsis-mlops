# 리플레이어 라운드 (다) — 실측 E2E 증거 기록물

> 성격: 빌드(코드)는 426b9b2 에서 끝남. 이 문서는 **"돌리고 기록"** — 실모델·실데이터로
> 다중 동시 스트림 + 환자별 위험도 Gauge 가 진짜 도는지 관측. 게이트 = 관측된 증거.
> 선행: (가)(e8ef105) 엔진, (나)(99b7c67) 단일 환자 실측, (다 코드)(426b9b2).

## 0. 환경 (라운드 (나)와 동일 — 교차검증 가능)

- **서빙**: 로컬 `uvicorn sepsis.serve.app:app`(127.0.0.1:8000). minikube 아님.
  - env: `SERVE_PER_PATIENT_GAUGE=1`(옵트인 ON), `SERVE_FEATURESET=vitals`, `DRIFT_CAL_TRIALS=20`(부팅 가속, p값 무관).
- **번들**: `deploy/artifacts/gru_vitals → gru_vitals@v0-base`, `run_id=8de08edb612749b2a45d3a029fac3123`,
  `tau=0.5732471412047744`, `input_dim=9` — **(나)와 같은 진짜 학습 모델**.
- **데이터**: `data/raw/training_setA/`(20,336명, in-distribution A).
- pre-flight: `/health`·`/schema` = (나)와 동일(9 features). 실측 중 422/500 **0건**.

## 1. 재현 커맨드

```bash
# 서빙 (옵트인 ON 으로 띄워야 Gauge 가 채워진다)
SERVE_PER_PATIENT_GAUGE=1 SERVE_FEATURESET=vitals DRIFT_CAL_TRIALS=20 \
  PYTHONPATH=src uv run uvicorn sepsis.serve.app:app --host 127.0.0.1 --port 8000

# 병동 — 3명 동시 재생
PYTHONPATH=src uv run python scripts/replay_ward.py \
  --psv data/raw/training_setA/p000018.psv \
  --psv data/raw/training_setA/p000001.psv \
  --psv data/raw/training_setA/p000002.psv \
  --base-url http://localhost:8000 --speed 36000

# Gauge 시계열(=Grafana 가 그릴 데이터)
curl -s http://localhost:8000/metrics | grep '^serve_pred_prob_latest'
```

## 2. 관측

### 2.1 다중 동시 스트림 작동 ✅ (`docs/reports/replay/replay_ward_3patients.txt`)
3명을 **동시**(스레드 3개, 공유 HttpSender)로 재생 — 211 timesteps, **500/422/error 0건**.
각 환자가 유일 `patient_id`(run_suffix)로 분리돼 섞이지 않음(F-c1 가드 + 서버 hidden state 분리).

| 환자 | 행수 | last_p | last_alarm | (나) 단일실행 대조 |
|---|---|---|---|---|
| p000018 (septic) | 134 | 0.9023 | True | 0.458–0.949, 후반 고위험 — **일치** |
| p000001 (control) | 54 | 0.8218 | True | 후반 0.82 — **상한 정확 일치** |
| p000002 (control) | 23 | 0.1954 | False | 0.195–0.464 — **하한 정확 일치** |

> 동시 실행 last_p 가 (나) 단일 실행 범위와 일치 → 멀티스트림이 추론을 오염시키지 않음(무상태 엔진 F5 입증).
> 교차검증: `serve_predict_requests_total=211`(=행 합), `serve_alarms_total=135`.

### 2.2 환자별 Gauge 채워짐 ✅ (`docs/reports/replay/replay_gauge_series.txt`)
```
serve_pred_prob_latest{patient_id="p000001-90da45-1"} 0.821753203868866
serve_pred_prob_latest{patient_id="p000002-90da45-2"} 0.19539159536361694
serve_pred_prob_latest{patient_id="p000018-90da45-0"} 0.9023167490959167
```
정확히 **3개 시계열**(환자당 1개), 값 = 각 환자 last_p. Grafana "Per-patient risk (latest p)" 패널의 원천.

### 2.3 위험도 "선"은 시간 가변 ✅ (`docs/reports/replay/replay_p000018_gauge_curve.tsv`)
Gauge 는 **최신값만** 보유하므로, 곡선은 Prometheus(또는 폴러)가 재생 중 반복 스크랩해 만든다.
p000018 을 느리게(speed=3000, ~1.2초/행) 재생하며 `/metrics` 를 ~3초 간격 폴링 → **40개 표본**:

```
초반: 0.56 → 0.54 → 0.63 → 0.56 → 0.60 → 0.78 → 0.75 … (τ 부근 깜빡임, 0.46–0.78)
중반: → 0.83 → 0.84 → 0.82 → 0.87 → 0.85 … (0.83–0.89 고원)
후반: → 0.92 → 0.94 → 0.94 → 0.89 → 0.89 (0.89–0.94 상승)
```
Gauge 값이 시간 따라 오르내림 → "위험도 선"이 진짜 그려진다(스냅샷 한 점이 아님). 추세 우상향은 (나) §2.2 와 일치.

### 2.4 카디널리티 가드(옵트인) 실서버 검증 ✅
별 포트(8001)에 **`SERVE_PER_PATIENT_GAUGE` 미설정(기본 OFF)** 서버를 띄우고 predict 1회:
`serve_predict_requests_total=1`(추론은 정상) 인데 `serve_pred_prob_latest` 시계열 **0개**.
→ 기본 OFF 가 핫패스에서 환자별 라벨을 안 만든다(무한 카디널리티 footgun 차단, 결정 §2 입증).

### 2.5 Grafana 패널 실렌더 ✅ (`docs/reports/img/replay_per_patient_risk.png`)
실제 모니터링 스택(`deploy/monitoring/docker-compose.yml` — prometheus 2s 스크랩 + grafana +
image-renderer)을 띄우고, 3명을 느리게(speed=4000) 동시 재생하며 Prometheus 가 `serve_pred_prob_latest`
를 **환자당 66 표본** 축적(p000018 진폭 min 0.461 / max 0.949 — (나) 0.458–0.949 와 일치).
Grafana `/render` API 로 "Per-patient risk (latest p)" 패널을 PNG 로 렌더:

- **p000018(septic, 파랑)**: 0.55→0.95 상승해 고원 — 고위험 지속.
- **p000001(control, 초록)**: 0.82 고원 — 위양성(§2.1 last_p 와 일치).
- **p000002(control, 주황)**: 0.20 횡보 — 깨끗한 음성.

세 환자의 위험도 **선**이 한 화면에 시간축으로 그려짐 = 라운드 (다) 목표 달성. 재생 종료 후
선이 평탄해지는 건 Gauge 가 최신값을 유지하기 때문(정상 — 곡선은 *재생 중* 구간이 본체).

## 3. 정직성 한계 (반드시 기록)

- **일화지 성능 지표 아님** — 환자 3명. AUROC/utility 주장 금지(h2/h3 몫). (나)와 동일 입장.
- **in-distribution(A)** 관측. cross-site(A→B) 주장 0. `cross_site_claim=False` 불변.
- **폴링 ≠ Prometheus 스크랩.** 2.3 은 수동 폴러로 Gauge 가변성을 입증한 것 — 실 운영은 Prometheus
  scrape_interval 이 표본 밀도를 정한다(빠른 재생이면 곡선이 성기게 보임. 데모는 speed 를 낮춰야 곱게 보임).

## 4. 산출물

- `docs/reports/replay/replay_ward_3patients.txt` — 3명 동시 재생 로그(211 steps).
- `docs/reports/replay/replay_gauge_series.txt` — 환자별 Gauge 3 시계열 스냅샷.
- `docs/reports/replay/replay_p000018_gauge_curve.tsv` — p000018 Gauge 시간 궤적 40 표본(수동 폴링, 위험도 선 raw).
- `docs/reports/img/replay_per_patient_risk.png` — **Grafana 패널 실렌더**(3명 위험도 선, §2.5).
- `deploy/monitoring/{docker-compose.yml,prometheus.yml}` — 재현용 로컬 모니터링 스택.
- 이 문서.

## 5. 결론

라운드 (다) 코드(멀티스트림 + 옵트인 Gauge)가 **실모델·실데이터에서 진짜 돈다**: 3명 동시 재생이
오염 없이 분리되고(F5), 환자별 위험도가 Gauge 로 노출되며(옵트인 ON), 그 값이 시간 따라 곡선을 그리고
(2.3), Grafana 패널에 세 환자 위험도 선으로 렌더되며(2.5), 기본 OFF 가 카디널리티 footgun 을 막는다
(2.4). 데이터 계층부터 시각화까지 사슬 전체가 실측으로 닫혔다.
