# 핸드오프 검토 — H4-재학습 (레드팀, 실행 명세)

- **대상**: `docs/design/h4/retrain/handoff.md` (초안)
- **대상 commit**: `8499d23`
- **검토일**: 2026-06-29
- **핵심 질문**: human-in-the-loop이 코드로 강제되고, B 분할 누수 없고, cross-site 과장 없고, 버전드 교체·롤백이 실제 되는가.
- **판정**: ⛔ **HOLD 1건 → 구현 금지.** human-in-loop(자동경로 0건 grep)·B 환자 단위 분할·cross-site 정직 표기는 잘 명세됨. 막히는 곳: **버전드 export 전환이 이미 배포된 H4s-c 서빙 규약(`gru_vitals` 고정)을 깨고, drift reference가 버전 비분리라 롤백 시 baseline 불일치** — "안전 교체·롤백"이 실제로는 서빙을 깨거나 롤백 후 드리프트가 어긋난다.

---

## PASS

- **human-in-the-loop [A]** — `promote.py`는 recommendation만 반환, **자동 재학습·교체 호출 0건**(PASS a#1 grep), action은 드리프트 종합(share+지속성, 성능 단독 아님 a#2), 재학습·교체는 사람 트리거/승인(c#3). 정책+grep 강제. PASS *(승인 게이트 코드 기법은 권고 1).*
- **B 분할 누수 [B]** — setB 환자 단위 B-retrain/B-holdout 분할, **B-holdout ∩ B-retrain = ∅**(PASS b#1), 학습=A-train+B-retrain, train-only 통계 재산출(b#2), 마스크 OFF·0-fill 부재(b#5). H1 규칙 연장. PASS.
- **cross-site 정직 [B] ★** — `validate.py`가 B-holdout을 **in-distribution**(새 데이터 성능 + A-val 무회귀)으로 표기, **cross-site 일반화 주장 금지**(A+B 학습이라 미관측 제3 분포 없음, 제3 병원 C 필요 한계 명시)(`:20,64,70`, PASS b#3·b#4). 과장 없음. PASS — 강함.
- **시뮬레이션 정직 [D]** — 라벨·개입 스트림 부재 → 지연 라벨·트리거 시뮬로 구조 검증, 실데이터 아님 명시(`:22,48`). PASS *(라벨 자체는 실제·지연만 시뮬임을 정밀화 = 권고 4).*
- **원자 번들 [C]** — 교체/롤백이 H4s-a 원자성(model+통계+τ+input_dim 동일 run) 유지(c#5). PASS *(단 버전 규약·reference = HOLD).*
- **실패 모드** — auto-call·환자누수·cross-site 과장·train-only 위반·overwrite·승인없는 교체·reference 미갱신 망라(`:102-109`). PASS.

---

## HOLD (수정 필요)

### HOLD-1 — 버전드 전환이 H4s-c 서빙 규약을 깨고, drift reference 롤백 불일치

- **항목**: H4r-c `scripts/h4/h4s_export_bundle.py` 버전드 확장(`:81`), `deploy.py` reference 갱신(`:82`), PASS c#1·c#4
- **문제**:
  1. **버전드 export가 배포된 서빙을 깬다**: 핸드오프는 export를 "고정 dir → `gru_vitals@<timestamp>`"로 바꾼다(`:81`). 그러나 H4s-c 서빙은 **`gru_vitals` 이름을 하드코딩**한다 — `deploy/Dockerfile:20` `SERVE_BUNDLE_DIR=/app/deploy/artifacts/gru_vitals`, `deploy/k8s/configmap.yaml:13` `RUN: gru_vitals`, `scripts/h4/h4s_c_smoke.py`의 `export(fs)`→`gru_{fs}` 매핑(`:45-47,75`). export가 `gru_vitals@<ts>`만 만들면 **SERVE_BUNDLE_DIR가 가리키는 `gru_vitals`가 부재 → 컨테이너 기동 실패/번들 로드 실패**. 즉 "버전드"가 기존 서빙과 비양립.
  2. **drift reference가 버전 비분리 → 롤백 baseline 불일치**: reference는 단일 파일 `data/drift/reference_{featureset}.npz`(`reference.py:71`, 덮어쓰기)다. deploy가 재학습 후 reference를 새 분포(A+B-retrain)로 갱신(`:82`)하면, **롤백(RUN→이전 버전)** 시 모델은 옛 버전으로 돌아가지만 reference는 **새 분포 그대로** → 롤백된 옛 모델이 *자신이 학습한 적 없는 baseline*에 대해 드리프트 측정 → 거짓 신호. 롤백이 안전하지 않다.
- **근거**: `h4_retrain_handoff:81,82`; `deploy/Dockerfile:20`, `deploy/k8s/configmap.yaml:13`, `scripts/h4/h4s_c_smoke.py:45-47,75`(gru_vitals 고정); `src/sepsis/drift/reference.py:71`(단일 파일).
- **제안**:
  - **버전 규약을 서빙과 일관**되게: 모든 번들을 버전 이름으로(베이스라인 포함, 예 `gru_vitals@v0`/`@<ts>`), **ConfigMap RUN·Dockerfile·h4s_c_smoke를 버전 RUN으로 갱신**(마이그레이션 명시). 또는 export가 **버전 dir + 활성 버전 별칭(`gru_vitals` → 활성 버전 심링크/복사)**을 유지해 SERVE_BUNDLE_DIR가 항상 유효하게. 어느 쪽이든 "기존 서빙 미파괴"를 PASS c#1에 assert.
  - **drift reference를 번들 버전에 키잉**(예 `reference_{fs}@<ts>.npz` 또는 메타에 bundle_version): 교체 시 새 reference, **롤백 시 이전 reference도 함께 복원**. PASS c#4를 "reference가 배포 버전과 키 일치 + 롤백 시 복원"으로 강화. 실패 모드에 "롤백 후 reference-모델 버전 불일치" 추가.

---

## 실행 전 권고 (비차단)

1. **사람 승인 코드 기법 명시 [A]** — `deploy.swap(approved: bool)` **기본값 없음 + 미승인 시 raise**; 스모크가 명시적으로 `approved=True`를 줘 사람 승인 모사, 미승인 시 raise를 assert. grep 범위는 **`promote.py`·`backfill.py`(신호층)가 `pipeline.run`/`deploy.swap`을 *호출(Call)* 하지 않음**으로 정밀화(정의는 `pipeline`/`deploy`에 정상 존재).
2. **재학습 HP·τ 명세** — 재학습은 **H2 동결 HP\***(배포 조합 gru/vitals) 재사용(재탐색 아님), **τ는 A-val에서 재선정**(또는 B-holdout? — 명시). 재현성·일관성.
3. **검증 임계 수치화** — "무회귀/나쁘지 않음"을 수치로: 새 모델 A-val·B-holdout 지표 ≥ 기존 − ε(ε 핸드오프 확정). PASS b#3·c-게이트에 박기.
4. **시뮬 정밀화** — 라벨(SepsisLabel)은 *실제*이고 **지연/도착 타이밍만 시뮬**임을 명시(라벨 자체를 합성하는 게 아님). 우산장수도 *개입 컬럼 부재*라 태깅 불가임을 backfill 출력에 표기.
5. **reference 갱신 순서·원자성** — 교체(RUN 스왑)와 reference 갱신의 순서/원자성 명시(권장: 새 버전 reference 먼저 생성 → RUN 스왑 → 활성 reference 포인터 전환), 부분 실패 시 정합.

---

## 다음 단계

**HOLD-1(버전 규약 ↔ 서빙 정합 + reference 버전 키잉/롤백) 해소 후 재검토.** 전부 PASS 전 구현(코드·디렉토리 생성) 금지(WORKFLOW §5·§6).

---

## 재검토 v2

- **대상**: `docs/design/h4/retrain/handoff.md` v2 (개정 이력 v2 — HOLD 1건 두 갈래 + 비차단)
- **검토일**: 2026-06-29
- **판정**: ✅ **PASS — HOLD 0건.** v1 HOLD 두 갈래 모두 해소, 신규 블로킹 모순 없음. → **다음은 H4r-a 구현 착수.** (구현-완성 권고 2 + cosmetic nit 1.)

### 회귀 검증 (요청 4항목)

**1. HOLD 1-(1) 버전드↔서빙 (gru_vitals 활성 별칭) → ✅ 해소.**
- `:86` "`gru_vitals`를 **활성 버전 별칭**으로 유지(H4s-c가 Dockerfile SERVE_BUNDLE_DIR·ConfigMap RUN에 gru_vitals 하드코딩 → 별칭이 활성 버전 dir 가리킴, 기존 서빙 미파괴)." 교체=별칭 전환(`:87`).
- PASS #1(`:91`) "gru_vitals 별칭이 활성 버전 가리킴 — **기존 서빙 로드 성공 assert**", 실패모드(`:111`) "별칭 부재로 기존 서빙 로드 실패". ✓

**2. HOLD 1-(2) reference 롤백 (번들 포함) → ✅ 해소.**
- `:86` "**drift reference를 번들에 포함**(버전 dir 안 reference.npz) — 모델과 한 단위 이동. 원자 번들(model+통계+τ+input_dim+reference 동일 버전)." 롤백 시 "모델·전처리·τ·reference 함께 복원, 별도 reference 덮어쓰기 없음(거짓 드리프트 방지)"(`:87`).
- PASS #4(`:94`) "reference가 번들 버전에 포함, 롤백 후 reference-모델 버전 일치(거짓 드리프트 없음)", PASS #5(`:95`) 원자성에 reference 포함, 실패모드(`:113`) "롤백 후 reference-모델 불일치(거짓 드리프트)". ✓

**3. 비차단 → ✅ 반영.**
- 사람 승인: `deploy.swap(approved)` 기본값 없이 `approved=False면 raise`(`:88`, PASS #3). ✓
- 재학습 HP\*·τ **재사용**(재탐색 아님, `:68`). ✓ (검증 게이트가 reused-τ 열화를 차단하므로 안전.)
- 시뮬: "라벨 자체는 실제(setB SepsisLabel), **지연만 모사**"(`:53`). ✓

**4. 신규 모순 → 없음.**
- **별칭+reference-in-bundle ↔ H4s-a 원자성**: `load_bundle_from_dir`는 model.pt/pre.npz/meta.json 3개만 읽음 → reference.npz 추가는 무해(서빙은 무시), 드리프트만 읽음. 같은 버전 dir = 원자성 유지. 충돌 없음.
- **↔ H4s-c**: 별칭 gru_vitals 유지로 Dockerfile/ConfigMap/h4s_c_smoke(export→gru_{fs}) 불변 동작.
- **watch 경계**: 재학습이 watch 신호를 읽되 자동 행동 없음(human-in-loop) — 경계 유지.

### 실행 전 권고 (구현 완성 — 비차단)
1. **드리프트가 *활성 번들의* reference를 읽도록 명시** — reference를 번들에 넣는 효과가 실제로 나려면 H4d-b 드리프트 모니터가 `data/drift/reference_*.npz`(독립 파일)가 아니라 **활성 번들 dir(gru_vitals/reference.npz)**를 로드해야 함. 안 그러면 롤백 정합이 무효(reference-in-bundle이 장식이 됨). 핸드오프에 "드리프트 reference 로드 = 활성 번들" 1줄 + PASS #4를 "드리프트가 롤백된 버전의 reference로 측정"까지 확장.
2. **새 버전이 *실행 중 컨테이너*에 도달하는 경로 명시** — H4s-c는 번들을 이미지에 COPY(빌드 시 baked). 재학습 새 버전이 런타임 서빙에 반영되려면 **`deploy/artifacts`를 볼륨 마운트**(별칭 전환이 즉시 반영) 또는 **재-export→재빌드→재배포**. 프로토타입 방식 1줄(권장: 볼륨). 별칭은 심링크/복사 중 택1 명시(Docker COPY 심링크 보존).
3. **(미세) τ 재선정 옵션** — reused-τ는 안전(게이트 보호)하나 재학습 모델은 보정이 달라질 수 있음 → A-val에서 τ 재선정도 고려(선택). 

### Cosmetic nit (비차단)
- `:5-6` "**개정 이력**" 헤더 2줄 중복 → 한 줄 삭제(이전과 동일 패턴, 구현 중 정리).

**결론: HOLD 0 → H4r-a 구현 착수.** 권고(드리프트가 번들 reference 읽기·런타임 전달 경로)는 H4r-b/c 구현 시 흡수.
