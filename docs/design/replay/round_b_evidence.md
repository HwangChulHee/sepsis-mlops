# 리플레이어 라운드 (나) — 실측 E2E 증거 기록물

> 성격: 빌드 아님 = **"돌리고 기록"**. 코드 변경 0(서빙·엔진 무수정). 게이트 = 관측된 증거.
> 선행: 라운드 (가)(e8ef105) — 엔진·.psv 어댑터·CLI. 실행일 기준 로컬 환경.

## 0. 환경 (실측 구성)

- **서빙**: 로컬 `uvicorn sepsis.serve.app:app`(127.0.0.1:8000). minikube 아님(엔진+모델을 k8s 네트워킹과 안 섞음).
- **번들**: `deploy/artifacts/gru_vitals → gru_vitals@v0-base` (실재 alias, export/train 불필요).
  - `meta.json`: `featureset=vitals`, `input_dim=9`, `tau=0.5732471412047744`, `hp.hidden=128`,
    `run_id=8de08edb612749b2a45d3a029fac3123` (진짜 학습 모델 — docs/reports/h2_results.md GRU/vitals 셀렉트).
- **데이터**: `data/raw/training_setA/` (20,336명, in-distribution A).

### pre-flight (계약 일치 — 422 선제 차단, F3)

```
/health → {"status":"ok","run_id":"8de08edb...","featureset":"vitals","input_dim":9}
/schema → {"featureset":"vitals","features":["HR","O2Sat","Temp","SBP","MAP","DBP","Resp","Age","Gender"],"n_features":9}
```

`/schema.features`(9개) == CLI `--featureset vitals` 컬럼 → 일치. 실측 중 422/500 **0건**.

## 1. 재현 커맨드

```bash
# 서빙 (별 터미널)
PYTHONPATH=src uv run uvicorn sepsis.serve.app:app --host 127.0.0.1 --port 8000

# 재생 + 캡처 (speed 36000 = 행당 0.1초; p값은 speed 무관)
PYTHONPATH=src uv run python scripts/replay/replay_patient.py \
  --psv data/raw/training_setA/p000018.psv --base-url http://localhost:8000 --speed 36000 \
  | tee docs/reports/replay/replay_p000018_septic.txt
# 대조군: p000001, p000002 동일
```

> `--run-suffix`는 CLI가 매 실행 uuid8 자동 부여 → 재실행해도 서버 hidden state 안 섞임(F4 회피).
> 라벨(SepsisLabel)은 **서버에 보내지 않는다**(featureset 9개만). 라벨은 "어디서 올라야 맞나" 눈대조용.

## 2. 관측 (§6 게이트 — 코드 PASS/FAIL 아니라 기록)

| 환자 | 라벨 | 행수 | onset_t | p 범위 | alarm=True | 요약 |
|---|---|---|---|---|---|---|
| **p000018** | septic | 134 | 125 | 0.458–0.949 | 121/134 | onset 한참 전부터 고위험 지속(마지막 20스텝 평균 p=0.916) |
| **p000001** | non-septic | 54 | — | 0.299–0.822 | 14/54 | 후반 상승해 τ 교차 → **위양성** |
| **p000002** | non-septic | 23 | — | 0.195–0.464 | 0/23 | 전 구간 τ 미만 — 깨끗한 음성 |

### 2.1 사슬 작동 ✅
세 환자 모두 T행 → CLI가 정확히 T번 전송, T줄 p 출력, `done. T timesteps sent.`. **500/422/error 0건**. 엔진→HttpSender→/predict→실모델 추론 사슬이 진짜 돈다.

### 2.2 p가 움직임 ✅ (상수 아님)
- **p000018(septic)**: t=0 p=0.458 → 초반 τ 부근 깜빡임 → 후반(t≈100–133) 0.85–0.95 **지속 고위험**.
  단조 증가는 아니고 시간 따라 들쭉날쭉(모델 특성) — *있는 그대로* 기록. 추세는 명확히 우상향.
- **p000001(control)**: t=0 p≈0.465 sub-τ → 꾸준히 상승 → t≈49–53 p 0.72–0.82.
- **p000002(control)**: 0.20–0.46 저공 횡보, 상승 없음.

### 2.3 alarm 천이 (τ=0.5732 교차)
- **p000018**: t=3에서 첫 τ 교차(p=0.603), 초반 on/off 깜빡임(t=9~12 잠깐 False), 이후 거의 상시 True(121/134).
- **p000001**: 초반 False 유지하다 후반 상승 구간에서 True로(14회) — 지속 위양성.
- **p000002**: 전 구간 False(0회).

### 2.4 onset 대조 (일화적 눈대조)
p000018은 라벨 onset(t=125)보다 **훨씬 전부터**(사실상 t=3 이후 대부분) p가 τ 위에 있었다. "조기 경보"의 그림은 성립하나, **너무 일찍·너무 자주 울리는** 쪽(민감/과경보)이다. 임상 라벨 onset과 알람의 정밀 정렬은 이 라운드 범위 밖(엄밀 평가 = utility score, h2_results.md 몫).

## 3. 정직성 한계 (반드시 기록)

- **일화(anecdote)지 성능 지표 아님.** 환자 3명 = 사례일 뿐. AUROC/utility/민감도-특이도 **주장 금지**.
  지표는 docs/reports/h2_results.md(GRU/vitals util 0.4087 셀렉트)와 H3 cross-site 평가의 몫.
- **in-distribution(A) 관측.** cross-site(A→B) 주장 **0**. `cross_site_claim=False` 불변.
- **위양성을 미화하지 않음.** "control" p000001이 14/54 알람을 냈다 — 이건 모델이 *이 환자에선* 과하게
  울렸다는 정직한 신호다(non-septic이라고 생리값이 정상이란 뜻은 아니나, onset 라벨이 없는데 알람이
  지속된 건 운영상 **알람 피로(false-alarm burden)** 문제의 실물 예시 — 이 프로젝트의 핵심 주제와 직결).
- **τ 고정**: alarm은 번들에 동결된 τ=0.5732(A-val 유래) 기준. 운영 중 τ를 라이브 데이터로 재선택하면
  누수(H3/H4 규칙). 여기선 동결값 그대로 관측만.

## 4. 산출물

- `docs/reports/replay/replay_p000018_septic.txt` — septic p 시퀀스(raw 증거, 134줄).
- `docs/reports/replay/replay_p000001_control.txt` — 위양성 control(54줄).
- `docs/reports/replay/replay_p000002_control.txt` — 저위험 control(23줄).
- 이 문서(`docs/design/replay/round_b_evidence.md`).
- **코드 변경 없음.** 커밋 = 증거 기록물 + reports 캡처만.

## 5. 다음 라운드 (다) 예고 — 이번엔 손대지 말 것

- Gauge `serve_pred_prob_latest`(환자별 최신 p) + Grafana 시계열 패널 — 한 환자 "위험도 선" 가시화.
  현재 `serve_pred_prob`는 Histogram(분포)이라 곡선이 안 그려짐. 서빙 프로덕션 코드 수정 필요 → 별도 결정.
- 다중 동시 스트림(병동) — 무상태 엔진(F5) 위에 N개 source 동시 구동. Gauge 카디널리티 결정과 함께.
