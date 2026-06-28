# 핸드오프 검토 — H4-재학습 (레드팀, 실행 명세)

- **대상**: `design/h4_retrain_handoff.md` (초안)
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

- **항목**: H4r-c `scripts/h4s_export_bundle.py` 버전드 확장(`:81`), `deploy.py` reference 갱신(`:82`), PASS c#1·c#4
- **문제**:
  1. **버전드 export가 배포된 서빙을 깬다**: 핸드오프는 export를 "고정 dir → `gru_vitals@<timestamp>`"로 바꾼다(`:81`). 그러나 H4s-c 서빙은 **`gru_vitals` 이름을 하드코딩**한다 — `deploy/Dockerfile:20` `SERVE_BUNDLE_DIR=/app/deploy/artifacts/gru_vitals`, `deploy/k8s/configmap.yaml:13` `RUN: gru_vitals`, `scripts/h4s_c_smoke.py`의 `export(fs)`→`gru_{fs}` 매핑(`:45-47,75`). export가 `gru_vitals@<ts>`만 만들면 **SERVE_BUNDLE_DIR가 가리키는 `gru_vitals`가 부재 → 컨테이너 기동 실패/번들 로드 실패**. 즉 "버전드"가 기존 서빙과 비양립.
  2. **drift reference가 버전 비분리 → 롤백 baseline 불일치**: reference는 단일 파일 `data/drift/reference_{featureset}.npz`(`reference.py:71`, 덮어쓰기)다. deploy가 재학습 후 reference를 새 분포(A+B-retrain)로 갱신(`:82`)하면, **롤백(RUN→이전 버전)** 시 모델은 옛 버전으로 돌아가지만 reference는 **새 분포 그대로** → 롤백된 옛 모델이 *자신이 학습한 적 없는 baseline*에 대해 드리프트 측정 → 거짓 신호. 롤백이 안전하지 않다.
- **근거**: `h4_retrain_handoff:81,82`; `deploy/Dockerfile:20`, `deploy/k8s/configmap.yaml:13`, `scripts/h4s_c_smoke.py:45-47,75`(gru_vitals 고정); `src/sepsis/drift/reference.py:71`(단일 파일).
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
