# 검토 — H4-서빙 (레드팀 게이트)

- **대상**: `design/h4_serving_decisions.md` (초안)
- **대상 commit**: `dbe5c22`
- **검토일**: 2026-06-28
- **핵심 질문**: 이 서빙 설계가 **train-serving skew·누수 없이**, **causal하게**, **자립적으로** 구현 가능한가.
- **판정**: ⛔ **HOLD 1건 → 핸드오프 금지.** 누수/causal 원칙(A 동결·단방향 hidden-state·마스크 OFF·운영 재정규화 금지)은 코드 근거상 건전. 막히는 곳은 **결정 1의 "모델·피처셋 독립 설정 교체"가 train-serving skew 통로** — 번들 원자성 미결정.

---

## PASS

- **결정 3 (hidden-state causal 동치)** — 단방향 GRU(`gru.py:27` `bidirectional=False`)에서 hidden state 이어가기는 전체 재입력과 **정확히 동일**(GRU 점화식 `h_t=GRU(x_t,h_{t-1})`). 미래 누수 없음. PASS [확인됨, 아래 §1차]. *(단 eval-mode·전층 h_n 보존·환자별 상태 격리는 핸드오프 — 권고 3.)*
- **결정 4 (A 동결 전처리, 누수 방지)** — 순서 ffill→fill(A평균)→clip(A범위)→z-score(A μ/σ), 전부 A 동결, 0-fill 금지, 마스크 OFF. **정규화 통계가 글로벌 A-train**(`normalize.py:31-35`, per-patient 아님)이라 스트리밍에서 미래 누수 없음. H3 누수규칙 정합. PASS [확인됨]. *(ffill 스트리밍 동치는 §1차+권고 2.)*
- **결정 2·6 (τ A-val 동결, 시뮬레이터 관찰 전용)** — τ는 A-val 동결, 운영/B 재선정 없음(H3 규칙). 시뮬레이터 B는 "재생만"(`:82`), 미래 미사용(`:79`). PASS.
- **결정 5 (FastAPI)** — 경량·async·pydantic, 표준 선택. PASS *(스키마가 피처셋 교체와 호환되려면 HOLD-1의 번들화 필요).*
- **PASS 기준 #2·#3** — bit-동일·상태 동치는 프로그래매틱(동일 입력 batch vs stream, ±tol). 설계상 achievable. PASS.

---

## HOLD (수정 필요)

### HOLD-1 — 결정 1: "모델·피처셋 독립 설정 교체"가 train-serving skew 통로 (번들 원자성 미결정)

- **항목**: 결정 1(`:24-32`), 결정 5 스키마(`:73`), PASS #2
- **문제**: 결정 1은 "모델·피처셋을 **설정값으로 받아 갈아끼움**"(`:26`)이라 하고, 일관성("전처리·input_dim까지 일관되게 바뀌는지")을 **검토 요청 항목으로 떠넘긴다**(`:32`) — 결정하지 않았다. 모델·피처셋·μ/σ·fill·clip·τ·input_dim이 **독립 노브**이면, `model=gru` + `featureset=vitals_labs`인데 **vitals 통계/τ/input_dim**을 로드하는 식의 **불일치 번들 = 즉각적 train-serving skew**(에러 없이 조용히 틀린 확률). 이것은 본 검토의 최우선 축([A] skew)을 정면으로 건드린다.
- **근거**: `:26,32`; H2 아티팩트는 **run 단위 번들**(`h2c:175,185,188,191-192` — `gru_{fs}.pt`+`pre_{fs}.npz`+json{hp,input_dim,τ,featureset}가 한 run에 묶임). 피처셋별 μ/σ·input_dim·τ가 전부 다름.
- **제안**: 결정 1을 **"설정은 *단일 조합(combo/run)*을 선택하고, 모델+피처셋+μ/σ·fill·clip+τ+input_dim을 그 한 run에서 원자적으로 로드"**로 명문화(독립 노브 금지). 입력 스키마(결정 5)도 로드된 번들의 featureset에서 파생. PASS #2에 "번들 원자성: 로드된 전처리·τ·input_dim이 모델과 동일 run 출처" assert 추가.

---

## 1차 확인 결과

- **hidden-state 이어가기 == 전체 재입력** [확인됨: 코드+구조] — `gru.py:27` 단방향 GRU. nn.GRU는 `(x_t, h_{t-1})→(out_t, h_t)` 점화식이라 1스텝 전진이 전체 재입력과 수치 동일(부동소수 ±1e-6). 단 ① `model.eval()`로 dropout off(결정적), ② 다층이면 전층 `h_n` 보존, ③ 현재 `GRUm2m.forward(x)`는 `out,_`로 **h_n을 버림**(`:33`) → 서빙은 `self.gru(x_t, h_prev)`를 쓰는 **stateful forward 추가 필요**(코드 추가, 설계 결함 아님).
- **ffill 스트리밍 == 배치 ffill** [확인됨: `missing.py:22-35`] — 배치 ffill은 "열별 직전 유효값 carry, 선두 NaN은 NaN 유지". 스트리밍에서 **상태=피처별 마지막 관측값, 미관측이면 NaN 유지**로 초기화하면 동일. *주의: 상태 초기값은 0/train평균이 아니라 NaN*(아니면 skew). 선두 미관측은 NaN→fill(A평균), 배치와 동일.
- **정규화 미래 누수 없음** [확인됨: `normalize.py:31-39`] — μ/σ는 글로벌 A-train(환자 전체통계 아님)이라 스트리밍 적용이 causal.
- **H2 GRU 아티팩트 서빙 로드 가능** [확인됨: H3-b에서 동일 로드 검증] — state_dict + npz{μ/σ·fill·clip} + json{hp·input_dim·τ}. GRUm2m 재구성 후 load_state_dict+eval.
- ⚠️ **pdm-mlops 자산 `[확인됨]`은 검증 불가** — CC는 pdm-mlops 레포를 못 본다(WORKFLOW §자립성). 결정 5·7·8·`:90,99`의 `[확인됨: pdm-mlops 자산]`(Prometheus/Grafana 구축, compose→K8s 전환 등)은 **1차 확인 불가** → 등급 과대표기. (권고 1.)

---

## 실행 전 권고 (비차단)

1. **pdm-mlops `[확인됨]` 하향** — CC 검증 불가이므로 `[우리 결정/경험]` 또는 `[검증 필요]`로(WORKFLOW §3). 설계 정당성엔 영향 없으나 등급은 정정. 결정 8이 이미 "핸드오프 자립 인라인"을 약속(`:101`)하므로 자립성 자체는 OK — 핸드오프가 Dockerfile·FastAPI 구조·K8s YAML(Deployment/Service/ConfigMap)을 **전부 인라인**(pdm 미참조)하면 됨.
2. **ffill 스트리밍 상태 명세(핸드오프)** — 상태 초기값 **NaN**(0/평균 금지), 피처별 마지막 관측 carry, 선두 미관측→fill(A평균). PASS #2에 batch-vs-stream bit-동일 테스트(동일 환자 시점열).
3. **상태관리 디테일(핸드오프)** — `GRUm2m`에 stateful forward(`gru(x_t,h_prev)→out,h_n`) 추가, `model.eval()`, 전층 h_n 보존, **환자별 상태 격리·새 환자 reset**(상태 누수=환자 간 오염 방지). PASS #3 동치 테스트.
4. **frozen-only 강제(핸드오프)** — 서빙 경로에서 `compute_norm_stats`·`compute_fill_mean`·`select_threshold` 미호출(H3-b AST grep 선례 재사용), 통계·τ는 아티팩트 로드값만. "운영 데이터 재정규화 금지"를 코드로.
5. **결정 7 메트릭 보강** — 드리프트(H4-드리프트) 토대로 **입력 피처 분포**(covariate shift)도 노출(현 목록은 예측분포·알람률=concept 쪽만). 입력 분포 없으면 covariate 드리프트 감지 불가.
6. **PASS #7 구체화** — "K8s 자립 명세"는 비프로그래매틱 → **docker build 성공 + `kubectl apply --dry-run`(또는 매니페스트 스키마 검증)**으로 떨어지게.
7. **시뮬레이터 B 가드** — B 재생은 관찰 전용(점수로 선택·튜닝 금지) 코드 주석/assert로 H3 규칙 재확인.

---

## 다음 단계

**HOLD-1(설정 번들 원자성) 해소 후 재검토.** 전부 PASS 전 `h4_serving_handoff.md`로 가지 않는다(WORKFLOW §5).
