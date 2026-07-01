# 검토 — H4-재학습 (레드팀 게이트)

- **대상**: `design/h4/retrain/decisions.md` (초안)
- **대상 commit**: `b963b66`
- **검토일**: 2026-06-29
- **핵심 질문**: 드리프트≠성능을 올바로 다루고, human-in-the-loop을 강제하며, 안전하게 교체하고, 데이터 한계에 정직한가.
- **판정**: ⛔ **HOLD 2건 → 핸드오프 금지.** 트리거 철학(드리프트 주도·성능 보조)·human-in-the-loop·우산장수 한계 정직성은 건전. 막히는 곳: **(1) "안전 교체·롤백"이 기존 export-overwrite 패턴과 비양립**(재학습이 살아있는 번들을 덮어쓰고 이전 버전 미보존 → 롤백 불가). **(2) 재학습 검증의 "cross-site(H3 방식)"가 봉인 setB를 반복 재사용 → selection-on-test 누수**(H3가 일회성 봉인으로 지킨 B를 재학습마다 모델 선택에 소모).

---

## PASS

- **결정 1 (드리프트 주도·성능 보조) [A]** — 드리프트 = "성능 하락 *위험*"(≠확정), action = "조사 권고"(자동 재학습 아님), 성능은 보조(라벨 지연·우산장수로 비권위). 일관. H3 0.41→0.25로 "분포 멀어지면 위험" 입증(`h3_results.md:12` gru/vitals A 0.4087→B 0.2466) [확인됨]. PASS *(성능 표면에 편향 라벨링은 권고 2).*
- **결정 2 (human-in-the-loop) [B]** — 결정=사람, 시스템=정보+파이프라인+안전교체, **자동 교체 금지**. 의료 경계 타당. PASS(정책) *(코드 강제 기법은 권고 1).*
- **결정 3 (우산장수 태깅 미구현) [A]** — PhysioNet에 개입/처방 컬럼이 **없음**(`config.py:21-39` — vitals+demo+labs, 제외=ICULOS/Unit/HospAdm, 치료 컬럼 0) → 태깅 근거 자체가 없어 미구현이 정직. 성능을 주 트리거에서 빼(결정 1) 편향 영향 격리. PASS [확인됨: config].
- **부속 (루프 폐쇄·한계 정직)** — 서빙→드리프트→재학습→번들→서빙. 시뮬레이션으로 *구조* 검증(효능 아님) 명시. PASS *(시뮬 범위 명문화는 권고 3).*
- **결정 4 train-only / reference↔서빙** — 부분 PASS: train-only 통계 재산출은 누수 원칙 정합; **reference 갱신은 원자 번들(H4s-c)로 skew 없음**(모델+통계가 한 run으로 함께 이동 → 새 서빙=새 통계+새 모델 일관, 전환은 ConfigMap RUN 원자 스왑). *단 overwrite=HOLD-1, B검증=HOLD-2.*

---

## HOLD (수정 필요)

### HOLD-1 — "안전 교체·롤백"이 export-overwrite 패턴과 비양립 (★안전)

- **항목**: 결정 5(`h4_retrain_decisions:71`), PASS #4
- **문제**: 결정 5는 "export 번들 패턴(`scripts/h4s_export_bundle.py`)으로 새 번들 생성 → ConfigMap RUN 전환, **이전 번들 보존·롤백**"이라 한다. 그러나 `h4s_export_bundle.export`는 **고정 dir `gru_<featureset>`를 `shutil.rmtree` 후 재생성**(덮어쓰기, 버전 없음)이다. 재학습이 같은 featureset를 export하면:
  1. **살아있는 서빙 번들을 in-place 덮어씀**(서빙 중 교체 위험).
  2. **이전 버전이 남지 않음 → 롤백 타깃 부재.** ConfigMap RUN은 *dir 이름*으로 전환(`deploy/k8s/configmap.yaml:13` RUN=gru_vitals, `deployment.yaml:34` `$(RUN)`)인데 이름이 동일해 **버전 구분·롤백 불가**.
- **근거**: `scripts/h4s_export_bundle.py`(`out=…/gru_{fs}; if out.exists(): shutil.rmtree(out)`); `deploy/k8s/configmap.yaml:13`, `deployment.yaml:34`.
- **제안**: **버전드 번들 dir**(예 `gru_vitals@<timestamp>` 또는 `gru_vitals_vN`)로 export(덮어쓰기 금지), **ConfigMap RUN을 그 버전 이름으로** 지정 → 새 버전 추가 후 RUN 스왑(원자), 이전 버전 보존(롤백=RUN을 이전 버전으로). export 스크립트에 versioned-out 옵션 추가. PASS #4에 "이전 번들 보존됨(롤백 타깃 존재) + 살아있는 번들 미덮어씀" assert.

### HOLD-2 — 재학습 검증이 봉인 B를 반복 재사용 → selection-on-test 누수 (★누수)

- **항목**: 결정 4(`:61` "cross-site 검증(H3 방식)"), 결정 5(`:71` "A-val + cross-site 나쁘지 않음")
- **문제**: H3에서 **setB는 일회성 봉인 held-out**(cross-site 일반화의 정직한 1회 측정)이었고, 프로젝트 전체가 B를 보호했다. 재학습 검증이 "cross-site(H3 방식)"로 매 사이클 **B에 대해 새 모델 vs 기존을 비교해 배포를 결정**하면, B가 **모델 선택에 반복 사용** → held-out 지위 소모(= H3가 막은 selection-on-test). "B에서 안 나빠지는 모델만 배포"를 반복하면 **B 과적합**, cross-site 일반화 주장이 무너진다.
- **근거**: `h4_retrain_decisions:61,71`; H3 누수 원칙(reports/h3_results.md, h3_decisions B 봉인); WORKFLOW §3 누수.
- **제안**: 재학습 검증의 cross-site 프록시를 **신규 운영 데이터의 시간적 홀드아웃**(배포 대상 분포)으로 둔다 — 봉인 B를 반복 소비하지 않음. 봉인 B는 (필요 시) **1회 한정 참조**로만 쓰고 그 소모를 명시. 결정 4/5에서 "cross-site = setB 재사용"인지 "신규 홀드아웃"인지 **명확화**(setB 재사용이면 누수, 금지). PASS #4의 "cross-site"를 신규 홀드아웃으로 정의.

---

## 1차 확인 결과

- **export 덮어쓰기(롤백 불가)** [확인됨: `scripts/h4s_export_bundle.py`] — 고정 dir rmtree·재생성, 버전 없음.
- **ConfigMap RUN = dir 이름 전환** [확인됨: `deploy/k8s/configmap.yaml:13`, `deployment.yaml:34`] — 동일 이름이면 버전 구분 불가(HOLD-1).
- **개입/처방 컬럼 부재** [확인됨: `config.py:21-39`] — 우산장수 태깅 근거 없음, 결정 3 한계 정직.
- **H3 cross-site 수치** [확인됨: `h3_results.md:12`] gru/vitals A 0.4087→B 0.2466(≈0.41→0.25). 결정 1 근거 유효.
- **H1~H2 파이프라인 존재** [확인됨: `src/sepsis/{data,train}`] — 재사용 가능. *단 H2는 6조합+사람 featureset 선택을 포함하므로 재학습은 배포 조합만 재실행해야(권고 4).*

---

## 실행 전 권고 (비차단)

1. **human-in-the-loop 코드 강제 [B]** — watch→action은 **recommendation 아티팩트만** 생성(드리프트→`train`/`fit`이나 ConfigMap write로 가는 **자동 경로 0건**, AST/구조 grep). 재학습·교체는 별도 수동 커맨드 + 승인 게이트. PASS #6에 "자동 스왑 경로 부재" assert.
2. **우산장수 편향 표면 라벨링** — 보조 성능을 사람에게 보일 때 "feedback-loop biased(개입 미관측)" 태그를 붙여 raw 수치로 오신뢰 방지(결정 3 인지를 표면까지).
3. **시뮬레이션 범위 명문화** — 라벨·개입 스트림 부재 → 시뮬은 **구조 검증만**(효능 아님): ① 의도적으로 *더 나쁜* 재학습 모델 → 검증 게이트가 **차단**, ② 롤백이 이전 번들 **복원**, ③ 드리프트 신호로 **자동 교체 안 됨**. 무엇을 주입·측정하는지 핸드오프에 적시.
4. **재학습 = 배포 조합만 재실행** — 전체 H2(6조합+사람 featureset 선택)가 아니라 **현재 배포된 조합(gru/vitals = h2c 경로)** 재학습. featureset 재선택은 사람 체크포인트로.
5. **검증 임계 구체화** — "나쁘지 않음"을 수치 마진으로(예 ΔPR-AUC/utility ≥ −ε). reference 갱신 시 "새 학습분포가 그 드리프트를 흡수 → 이후 동일 시프트는 미플래그"가 의도임을 명시.
6. **번들 보존 정책** — 보존 버전 수·정리(N개 유지) 정의(디스크 무한증가 방지), 롤백 SLA.

---

## 다음 단계

**HOLD 2건(버전드 번들·롤백 / 봉인 B 재사용 누수) 해소 후 재검토.** 전부 PASS 전 `design/h4/retrain/handoff.md`로 가지 않는다(WORKFLOW §5).

---

## 재검토 v2

- **대상**: `design/h4/retrain/decisions.md` v2 (개정 이력 v2 — HOLD 2건)
- **검토일**: 2026-06-29
- **판정**: ✅ **PASS — HOLD 0건.** v1 HOLD 2건 해소, 신규 블로킹 모순 없음. → **다음은 `design/h4/retrain/handoff.md`.** (cosmetic nit 1 + 정직성 권고 1.)

### 회귀 검증 (요청 3항목)

**1. HOLD-1 (버전드 번들·롤백) → ✅ 해소.**
- 결정 5(`:79`): export를 **버전드로 확장** — 고정 dir 덮어쓰기 대신 **`gru_vitals@<timestamp>` 버전 dir**에 생성(이전 보존, 살아있는 번들 미덮어씀), ConfigMap **RUN을 버전 지정으로 원자 스왑**, **롤백=RUN을 이전 버전 dir로**. PASS #4(`:98`)에 "이전 보존+살아있는 번들 미덮어씀+롤백" 반영. → 현재 overwrite 패턴의 롤백 불가 문제 해소.

**2. HOLD-2 (봉인 B 반복 소모 누수) → ✅ 해소.**
- 결정 4(`:68`): "신규 운영 데이터 없음(A/B 고정)" 인정 → **B를 운영 데이터로 가정**, **환자 단위 B-retrain/B-holdout 분할**(holdout=검증 전용, retrain과 분리) → **A-train+B-retrain 재학습, B-holdout 검증**. 검증 게이트(결정 5 `:79`)는 "A-val + **B-holdout**"(봉인 B 재사용 아님).
- **B 역할 전환 명시**(`:69`): H3 B=cross-site 평가 봉인(1회 관찰·모델선택 미사용) → H4 B=운영 데이터(학습 입력+자체 holdout). 봉인 B를 검증에 **반복 소모하던 selection-on-test 누수 해소** — B는 검증 대상이 아니라 학습/홀드아웃 재료. ✓
- B-holdout은 B-retrain과 **분리된 새 홀드아웃**(`:68`, PASS #3 `:97` 환자 단위) → 분할 내부 누수 없음. ✓

**3. 신규 모순 → 없음.**
- **B 학습 사용 ↔ H3 결과**: 충돌 없음. H3는 B를 **봉인해 1회 관찰만**(모델 선택 미사용) → cross-site 측정은 *이미 확정*. H4가 그 *이후* B를 운영 데이터로 재활용해도 **H3 기록은 불변**(DDD `:70` 명시). 일회성 held-out을 제 목적(H3 주장)에 소진한 뒤 신규-데이터 시뮬 재료로 전환 — 방법론적으로 정합.
- 요약(`:15`)·범위표·PASS(#3·#4)가 "B=운영 데이터·버전드 번들"로 일관. retrain 검증을 "cross-site"가 아니라 "B-holdout"으로 정직 표기(과대주장 없음).

### Cosmetic nit (비차단)
- `:64`·`:66` **결정 4 헤더 2줄 중복**(`:64`은 구 제목 "…+ 새 데이터" 고아 헤더, `:66`이 실제 v2 헤더+본문). 본문 모순은 없음(중복 헤더만) → `:64` 한 줄 삭제. (이전 botched-edit 패턴과 동일, 기계적.)

### 실행 전 권고 (비차단)
- **post-retrain cross-site 측정 불가 명시**: A·B 둘 다 학습에 들어가면 재학습 모델엔 **미관측 제3 분포가 없음** → B-holdout은 *in-distribution* 검증(새 데이터 성능+A 무회귀)이지 cross-site 일반화 *주장*이 아님을 한 줄 명시(정직성). 실운영에선 신규 데이터 홀드아웃이 이 역할.
- (v1 권고 1·3·5 유효) human-in-loop 자동경로 0건 grep(PASS #6 이미 반영), 시뮬 범위(게이트 차단·롤백 복원), 검증 임계 수치화.

**결론: HOLD 0 → `design/h4/retrain/handoff.md` 작성으로 진행.** cosmetic nit·권고는 핸드오프에서 흡수.
