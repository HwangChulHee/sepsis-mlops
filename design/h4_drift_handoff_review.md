# 핸드오프 검토 — H4-드리프트 (레드팀, 실행 명세)

- **대상**: `design/h4_drift_handoff.md` (초안)
- **대상 commit**: `22af19b`
- **검토일**: 2026-06-29
- **핵심 질문**: 거리지표 감지가 **자기상관·거짓경보 없이** 구현되고, **watch 범위 경계**가 지켜지며, **서빙 토대와 정합**하는가.
- **판정**: ⛔ **HOLD 2건 → 구현 금지.** 범위 경계(watch-only, 라벨 미사용)·reference RAW·합성주입 보정·KS/α 미사용은 잘 명세됨. 막히는 곳: **(1) 드리프트 수집 훅(`metrics.record`)에 patient_id가 없어 "환자당 1관측" 집계가 데이터 소스상 불가** — 자기상관 회피의 토대가 무너짐. **(2) reference 단위 미고정 + Evidently 거리지표 강제 미지정** — 작은 윈도우에서 Evidently가 KS로 자동 폴백해 결정 3 위반, reference/current 단위 불일치로 거리 무효.

---

## PASS

- **§범위 경계 [B]** — watch-only, 성능·라벨·action·재학습은 H4-재학습으로 분리(`:17-20`), PASS #3(`:82`)·실패모드(`:102`)가 월권 차단. 라벨 미사용 명시. PASS(경계 설계 우수).
- **H4d-a #1 reference RAW + 결측률 [A]** — `reference.py`가 A-train **RAW 분포**(μ/σ 정규화 통계 아님) + 피처별 **결측률** 동결(`:52,57`), `split.split_cross_site`의 A_train로 산출 가능(`split.py:28`). PASS *(단 단위는 HOLD-2).*
- **거리지표 채택 [A]** — Wasserstein/PSI/JS, KS p값·α·Bonferroni 미사용(`:23,61`), 실패모드로 강제(`:99`). 결정 3·4 정합. PASS *(Evidently 강제 미지정은 HOLD-2).*
- **합성 주입 경험적 보정 [A]/[D]** — `synthetic.py`로 주입 감지 + 비주입 FPR≈목표(`:54,59-60`), 임계를 이걸로 보정. 프로그래매틱 방향 타당. PASS *(단 입력 단위가 HOLD-1에 의존).*
- **윈도우 분리 [C]** — 드리프트 윈도우를 서빙 per-pid hidden state와 별도 저장(`:74`), 오염 없음. PASS *(단 pid 수집은 HOLD-1).*
- **Grafana 자립 [C]** — provisioning JSON, pdm 미참조 표준(`:77`). PASS *(검증 방식은 권고).*

---

## HOLD (수정 필요)

### HOLD-1 — 드리프트 수집 훅에 patient_id 부재 → "환자당 1관측" 집계 불가 (★자기상관 토대)

- **항목**: §환경/입력(`:14`), H4d-b `window.py`(`:74`), H4d-a `distance.py`(`:53`), PASS #2
- **문제**: 핸드오프는 드리프트 관측원을 **`serve/metrics.py`의 `record(raw_row)` 경로**로 못박는다(`:14,74`). 그런데 `record`의 시그니처는 `record(latency_s, p, alarm, raw_row, feature_names)` — **patient_id가 없다**(`metrics.py:28`). `app.predict`는 `req.patient_id`를 갖지만 `metrics.record` 호출에 **넘기지 않는다**(`app.py:64`). 따라서 명시된 데이터 소스로는 **윈도우를 환자별로 묶을 수 없고**, 결정 4·핸드오프의 핵심인 **"환자당 1관측(시점 자기상관 회피)"가 구현 불가**. 시점 단위로밖에 못 모으면 자기상관 → FPR 폭증(이 단계가 막으려던 바로 그것).
- **근거**: `h4_drift_handoff:14,53,74`; `serve/metrics.py:28`(pid 없음); `serve/app.py:64`(pid 미전달).
- **제안**: 드리프트 윈도우 수집이 **(patient_id, raw_row)** 를 받도록 훅 변경 — (권장) `app.predict`에서 `req.patient_id`와 함께 드리프트 윈도우에 적재(별도 drift-collect 호출), 또는 `metrics.record`에 `patient_id` 인자 추가. `window.py`는 `(pid, row)` 튜플을 저장해 윈도우 내 **환자별 집계**(요약) 후 거리 계산. PASS #2를 "윈도우가 pid로 환자별 집계됨"으로 강화.

### HOLD-2 — reference 단위 미고정 + Evidently 거리지표 강제 미지정 (★거리검정 유효성)

- **항목**: H4d-a `reference.py`(`:52`)·`distance.py`(`:53`), H4d-b `detector.py`(`:75`), PASS #2·#5
- **문제**:
  1. **단위 불일치 위험**: current는 **환자당 1관측 요약**(예 마지막/평균)인데, `reference.py`는 "A-train RAW 값 분포"라고만 함(`:52`) — **per-timestep인지 per-patient 요약인지 미고정**. 거리(Wasserstein/PSI/JS)는 두 분포가 **동일 단위**여야 유효한데, reference=시점·current=환자요약이면 집계가 분포 모양을 바꿔 **거짓 드리프트/은폐**. reference도 **동일 요약으로 환자당 1관측** 산출해야 함(미명시).
  2. **Evidently가 작은 윈도우에서 KS로 자동 폴백**: Evidently 기본은 **n>1000 Wasserstein, n≤1000 KS** [확인됨: Evidently 문서]. 의료 저변동 윈도우(일/주, 환자당 1관측이면 환자 수가 수백일 수 있음)는 **n≤1000 → KS 자동 선택** → 결정 3("KS 안 씀")을 **자기도 모르게 위반**. `detector.py`(`:75`)는 "Evidently 기본 Wasserstein>1000"에 의존만 함.
- **근거**: `h4_drift_handoff:52,53,75`; Evidently n-의존 기본(§1차).
- **제안**: (1) `reference.py`가 current와 **동일한 환자당-1관측 요약**으로 reference를 산출·동결(요약 함수 공유). PASS #1/#2에 "reference 단위 == current 단위" assert. (2) `detector.py`에서 **`num_stattest`를 명시 고정**(예 `num_stattest='wasserstein'` 또는 `'psi'`, 결측/범주 `'jensenshannon'`)해 **n과 무관하게 거리지표 강제**. PASS #2/#5에 "stattest가 거리지표로 고정(KS 자동선택 차단)" 추가. 실패모드에 "Evidently가 KS 자동선택" 추가.

---

## 실행 전 권고 (비차단)

1. **환자별 요약 함수 고정** [A] — "마지막값 vs 평균"(`:24,53`)을 하나로 확정하고 reference와 공유. *마지막값* = 최신 상태(권장, 입원 중 마지막 관측), *평균* = 평활. **per-patient 요약은 within-stay 시간 드리프트엔 둔감**(인구 covariate 드리프트엔 적절)임을 한계로 문서화.
2. **단일 엔진 권장** [D] — `distance.py`(자작)와 Evidently 두 거리 구현이 갈리면 PASS #5(엔진 정합)가 불일치 위험. 권장: **임계 보정도 Evidently 거리로** 수행(단일 엔진), 또는 `distance.py`를 *검증 오라클*로 명시 라벨링.
3. **작은/빈 윈도우 처리** [A] — 환자 수가 임계 미만이면 거리 추정 불안정 → **insufficient-data 상태**(드리프트 미판정)로 두고 watch 보류. 실패모드/PASS에 반영.
4. **결측률 거리 정의 명시** [A] — 결측률은 분포가 아니라 비율(Bernoulli) → JS는 2범주 분포로 계산하거나 `|Δ비율|` 임계. `distance.py`에서 방식 명시.
5. **PASS #4(Grafana) 프로그래매틱화** [D] — "대시보드 로드 가능"(`:83`)은 시각 검증 → **JSON 파싱 + 기대 패널/데이터소스 키 존재**로 떨어지게(전체 Grafana 기동 불필요).
6. **window↔reference 단위 일치 assert를 게이트로** — HOLD-2(1)의 런타임 가드.

---

## 1차 확인 결과

- **patient_id 부재**: `metrics.record`(`metrics.py:28`)에 pid 없음, `app.predict`가 미전달(`app.py:64`) — 환자별 집계 불가 [확인됨: 코드].
- **Evidently n-의존 기본**: >1000 obs 수치형 Wasserstein, **≤1000 KS**, 범주/저unique JS, 임계 0.1, `num_stattest`로 오버라이드 가능, 다중검정 α 보정 없음 [확인됨: Evidently 문서 검색]. → 작은 윈도우 KS 폴백 위험(HOLD-2).
- **A-train 추출**: `split.split_cross_site` → `A_train` pids(`split.py:28`), cache로 raw 로드 — reference 산출 가능 [확인됨: 코드].
- **범위 경계**: 핸드오프가 라벨·성능·action을 전부 H4-재학습으로 분리(`:17-20,91-93`) — watch-only 명확 [확인됨: 문서].

---

## 다음 단계

**HOLD 2건(수집 훅 pid · reference 단위+Evidently 거리 강제) 해소 후 재검토.** 전부 PASS 전 구현(코드·디렉토리 생성) 금지(WORKFLOW §5·§6).
