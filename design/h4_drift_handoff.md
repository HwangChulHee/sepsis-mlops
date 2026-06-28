# H4-드리프트 구현 핸드오프 — covariate 드리프트 감시 (watch까지)

> **설계 근거**: [`h4_drift_decisions.md`](h4_drift_decisions.md)(v2, 검토 PASS `3eb98bd`). 실행 명세로 번역.
> **워크플로우**: [`WORKFLOW.md`](WORKFLOW.md). 자립형(인프라·도구 버전 인라인).
> **개정 이력**
> - **v2 (2026-06-28)** — 핸드오프 검토 `25d490b`의 HOLD 2건 + 비차단 반영
>   - HOLD 1(pid 배관): 수집 훅이 `(patient_id, raw_row)`를 받도록 — app.predict가 pid 적재, window가 pid로 환자별 집계(현재 record/predict에 pid 없어 "환자당 1관측" 구현 불가했음).
>   - HOLD 2(reference 단위 + Evidently KS 폴백): reference도 current와 **동일 환자당 요약** 단위로 산출(단위 일치 assert). Evidently `num_stattest`를 거리지표로 **명시 고정**(환자 수백이면 기본이 KS로 폴백 → 결정 3 위반 방지).
>   - 비차단: 환자별 요약=**마지막값**(within-stay 둔감 명시), **단일 엔진**(Evidently 메인, distance.py는 합성검증용), insufficient-data 상태(작은/빈 윈도우), 결측률 거리 정의, PASS Grafana=JSON 파싱+패널 키 검증.
> - v1 (2026-06-28) — 초안. 범위 = **감지 + watch 신호 + 시각화까지**. 성능 결합 알람·action 승격·재학습 트리거는 H4-재학습(라벨 의존이라 경계 분리). DDD 비차단 흡수: 합성주입 보정 절차·환자당 1관측 집계·watch 프레이밍·결측률 1급 신호.

---

## 0. 공통 규칙 (자립형)

### 환경 / 입력
- 기존 환경 + **Evidently**(드리프트 감지) 추가. CPU. 외부 레포 참조 금지(도구 버전·Grafana provisioning 인라인).
- 입력: **A-train reference**(H1 캐시/split로 산출, 아래 환자당 요약 단위), 서빙 입력 윈도우. 수집 연결점 = `app.predict`가 **`(patient_id, raw_row)`를 윈도우에 적재**(현재 `metrics.record`·`app.predict`에 pid 없음 → pid를 함께 넘겨야 환자별 집계 가능). patient_id는 서빙이 이미 per-pid 상태에 쓰는 식별자 재사용.
- 재사용: `src/sepsis/data/{split,cache}.py`(A-train 추출), `serve/bundle.py`(featureset → 감시 피처 K).

### ★ 범위 경계 (중요)
- H4-드리프트 = **입력 분포 감시 + watch 신호 생성 + 대시보드**까지. 
- **하지 않음**: 성능(라벨) 결합 알람, watch→action 승격, 재학습 트리거 — 전부 **H4-재학습**(라벨 백필·우산장수 보정과 묶음). 본 단계는 라벨 미사용.
- watch = "드리프트 감지됨"의 관찰 신호일 뿐, 조치 알람 아님.

### 통계 프레임 (DDD 결정 3·4)
- 거리지표만: **Wasserstein/PSI(수치형) · JS(범주·결측률)**. KS p값·분석적 α·Bonferroni 쓰지 않음(거리지표라 p값 없음).
- **검정 단위 = 환자당 1관측**(시점 raw는 자기상관 → iid 붕괴). **환자별 요약 = 마지막 관측값**(서빙 최신 상태 반영; within-stay 변화엔 둔감함 명시). reference·current **둘 다 동일 요약 단위**로 산출(단위 일치 assert — 다르면 거리 무의미).
- **거짓경보 통제 = 합성 주입 경험적 보정**(분석적 α 아님).

### 진행 규칙
- 각 토막 commit & push. PASS 프로그래매틱, 실패 시 정지·보고.
- **H4d-a PASS → 자동 b** (이상 없으면). a 실패 시 정지(감지 엔진이 토대).

### 디렉토리 (생성)
```
src/sepsis/drift/
  reference.py     # H4d-a: A-train RAW reference + 결측률 동결
  distance.py      # H4d-a: 거리지표(Wasserstein/PSI/JS), 환자당 1관측
  synthetic.py     # H4d-a: 합성 드리프트 주입 검증
  detector.py      # H4d-b: Evidently 통합, 윈도우 수집
  window.py        # H4d-b: raw 입력 윈도우(per-pid 상태와 분리)
  watch.py         # H4d-b: watch 신호 → Prometheus
deploy/grafana/    # H4d-b: 대시보드 provisioning JSON
scripts/ h4d_a_smoke.py · h4d_b_smoke.py
```

---

## H4d-a — reference + 거리지표 엔진 + 합성주입 검증 ★토대

### 범위
A-train RAW reference 동결 + 거리지표 계산 + 합성주입으로 거짓경보 보정. 감지 정확성의 토대.

### 구현
- `reference.py`: H1 캐시/split에서 **A-train reference**(정규화 전 raw 원값) — **환자당 요약(마지막 관측값)으로 산출**, current와 동일 단위. 피처별 값 분포 + **피처별 결측률**. 저장(재현 가능). *μ/σ 정규화 통계가 아니라 raw 값 분포, 그리고 시점이 아니라 환자당 1관측 단위임에 주의.*
- `distance.py`: 거리지표(**Wasserstein/PSI** 수치형, **JS** 결측률·범주) — **합성 주입 검증·임계 보정 전용**(H4d-a). 운영 감지의 진실원천은 Evidently(H4d-b) 단일 엔진으로, distance.py와 갈리지 않게 동일 지표·임계 사용. reference·current 단위 일치 assert. 결측률 거리 = 피처별 결측 비율 분포의 JS.
- `synthetic.py`: **합성 드리프트 주입 검증** — (1) 알려진 분포 이동(예: 특정 피처 평균 shift, 결측률 증가)을 current에 주입 → 감지되는지. (2) 비주입(reference 재샘플) → 거짓경보율이 목표(≈5%) 근처인지. 이 결과로 **거리 임계를 경험적 보정**(PSI 0.1/0.2 관행을 출발점으로 조정).

### PASS 기준 (assert)
1. A-train reference + 결측률 동결, **환자당 요약(마지막값) 단위**로 산출(정규화 통계 아님 + 시점 아님 확인).
2. 거리지표(Wasserstein/PSI/JS) 계산, **검정 단위가 환자당 1관측**(시점 직접 아님). **reference·current 단위 일치 assert**.
3. **합성 주입 감지**: 알려진 shift/결측률 증가 주입 시 해당 피처 거리가 임계 초과.
4. **거짓경보 보정**: 비주입(reference 재샘플) 시 거짓경보율 ≈ 목표(≈5%). 임계가 이 보정으로 설정됨.
5. KS p값·분석적 α·Bonferroni 미사용(거리지표만).

### 진행
- 5개 PASS → 자동 H4d-b. 실패 시 정지·보고.

---

## H4d-b — Evidently 통합 + 윈도우 + watch 신호 + Grafana

### 범위
Evidently로 reference vs 최근 윈도우 감지, watch 신호 노출, Grafana 대시보드. (알람·action 아님.)

### 구현
- `window.py`: 서빙 입력 **`(patient_id, raw_row)`**를 raw 윈도우로 수집(최근 N건), **patient_id로 환자별 집계**(환자당 마지막값 1관측). **서빙 per-pid hidden state와 분리**(별도 저장, 환자 상태 오염 없음). **환자 수 부족 시 insufficient-data 상태**(거리 강제계산 금지). 의료라 분석 주기 느슨(설정).
- `detector.py`: **Evidently**로 reference vs 윈도우 드리프트 리포트. **`num_stattest`를 거리지표로 명시 고정**(Wasserstein 또는 PSI) — 환자당 1관측이면 표본이 환자 수백이라 Evidently 기본이 KS로 폴백(n≤1000)하므로, 자동 선택을 차단하고 거리지표 강제(결정 3 준수). 환자당 1관측 집계 입력. 피처별 거리 + 데이터셋 드리프트(드리프트 피처 비율).
- `watch.py`: 드리프트 감지 결과를 **watch 신호**로 Prometheus 노출(피처별 거리 gauge, watch 상태). **알람·승격 없음** — 관찰 신호만.
- `deploy/grafana/`: 대시보드 **provisioning JSON**(피처별 거리 시계열·결측률·watch 상태·예측확률 분포). pdm 미참조, 표준 Grafana provisioning.

### PASS 기준 (assert)
1. `(patient_id, raw_row)` 윈도우 수집, patient_id로 환자별 집계, 서빙 per-pid 상태와 분리(오염 없음). 환자 수 부족 시 insufficient-data.
2. Evidently 드리프트 리포트 생성, **`num_stattest`가 거리지표로 고정**(KS 자동폴백 차단 — 환자 수백에도 거리지표).
3. watch 신호 Prometheus 노출(피처별 거리·watch 상태). **알람/action 없음**(범위 경계 확인).
4. Grafana provisioning JSON 존재·**유효(JSON 파싱 + 필수 패널 키 검증)**.
5. 합성 주입(H4d-a) 시 Evidently도 동일 피처 감지(단일 엔진 정합).

### 진행
- PASS → H4-드리프트 완료. 보고 후 멈춤 → 다음은 H4-재학습.

---

## 범위 외 (H4-재학습)
- 성능(지연 라벨 백필) 결합 알람, watch→action 승격, 우산장수 intervention 태깅 적용
- 재학습 트리거·번들 교체(서빙 export 패턴 위에)
- concept drift 대응

## 실패 모드 (정지 트리거)
- reference가 raw 아닌 정규화 통계 / 결측률 reference 누락
- 검정 단위가 시점(자기상관) — 환자당 1관측 아님 / reference·current 단위 불일치
- 수집 훅에 patient_id 없음(환자별 집계 불가) / Evidently가 KS로 폴백(num_stattest 미고정)
- KS p값·분석적 α·Bonferroni 사용(거리지표 위반)
- 합성 주입 미감지 또는 비주입 거짓경보율 ≫ 목표
- 윈도우가 서빙 per-pid 상태 오염
- watch가 알람/action으로 월권(범위 경계 위반)
- Evidently 감지가 엔진(H4d-a)과 불일치
- 위 중 하나라도 → 정지·보고.

## 검토 요청 (h4_drift_handoff_review.md 용)
- 환자당 1관측 집계가 드리프트 신호를 죽이지 않는지(요약 방식 타당성).
- 합성 주입 검증·거짓경보 보정이 프로그래매틱한지(PASS #3·#4).
- reference가 RAW인지(정규화 통계 아님), 결측률이 1급 신호로 포함되는지.
- 윈도우가 서빙 상태와 분리되는지, Evidently 버전·거리기반이 결정과 정합하는지.
- watch가 알람·action으로 넘어가지 않는지(범위 경계).