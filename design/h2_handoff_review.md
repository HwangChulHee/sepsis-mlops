# 핸드오프 검토 — H2 (레드팀, 실행 명세 검토)

- **대상**: `design/h2_handoff.md` (초안)
- **대상 commit**: `9e5e3c0`
- **검토일**: 2026-06-28
- **핵심 질문**: 이 문서만으로 결정이 **의도대로 구현**되고 **게이트가 실제로 작동**하는가.
- **판정**: ⛔ **HOLD 3건 → 핸드오프 수정 필요, 구현 진입 금지.** (구조·진행로그·smoke 정합·자동/사람 경계는 PASS. 막히는 곳은 utility 인라인 *완전성*과 선택 절차 *순서*, 실패모드 *완전성*.)

---

## PASS (실행 가능 확인)

- **§0 분할/누수 원칙** — A-train-only 통계·B 봉인 명시(`h2_handoff:16-18`), H1 모듈 그대로 재사용. 정합. *(단 동적 B-guard 기법은 권고 1.)*
- **§0 재사용·디렉토리** — smoke 배선 승격 경로(`:13-14,41`)·신설 모듈 트리 명확. PASS.
- **진행 로그 [D]** — ETA = `elapsed/done*(total-done)`(`:23`), **터미널+파일 양쪽**(`:21-22`), 트리=trial 단위·GRU=epoch+batch 단위(`:24-25`) 모두 명세. [D] PASS.
- **H2-b HP 정책** — "vitals_labs 1회 탐색→동결→두 피처셋 공통"(`:77,83`), 모델당 동일 trial 수·동일 search space(`:81`,PASS `:89-90`), `scale_pos_weight` 고정(경미 반영, `:81,94`). 결정 6+v2 경미 정확 반영. PASS.
- **H2-b robustness [A]/[B]** — vitals 자체최적 vs 동결HP-vitals 차이를 **수치 산출·로깅**(`:84`), PASS는 "차이 수치 산출"(`:93`)이라 *magnitude 게이트가 아닌 산출-여부 게이트* → 진단용으로 적절. H2-d 결과표에 병기(`:134`)되어 **사람 체크포인트에 편향 크기가 판단 재료로 실제 제공**됨. [B] 두 번째 질문 충족. PASS.
- **자동 vs 사람 [B]** — 자동 a→b→c→d이되 "첫 실행이므로 각 토막 PASS 보고하고 멈춤"(`:32`)으로 매 토막 사람 확인 삽입. PASS 항목 중 *사람 눈*이 필요한 건 H2-a #3(12시점 표)뿐인데 이는 값 인라인 시 기계판정 가능(→HOLD-1). 그 외 숨은 사람판단 없음. PASS.
- **H2-c ↔ smoke 정합 [C]** — 단방향(`bidirectional=False`)·loss 마스킹·masked PR-AUC(`:107-108`)가 `scripts/smoke_m2m.py`의 `GRUm2m`(:39-48)·`run_train_epoch`(:103-114, `(loss_el*V).sum()/V.sum()`)·`evaluate`(:117-138, masked vs unmasked)와 일치. 풀 규모 추가분(조기종료·HP탐색·MLflow)도 식별됨. PASS *(단 utility 평가·τ 결합 순서는 HOLD-2).*
- **utility 인라인 상수값** — `dt −12/−6/+3`, `max_u_tp=+1`, `min_u_fn=−2`, `u_fp=−0.05`, `u_tn=0`, `m1=+1/6·m2=−1/9·m3=−2/9`(`:57-59`)이 공식 코드와 **전부 일치**(§1차 확인). 값 자체는 PASS. *(완전성은 HOLD-1.)*

---

## HOLD (수정 필요)

### HOLD-1 — H2-a utility **인라인 명세 불완전** + 자립성 내부모순 (가장 중요)

- **항목**: H2-a 구현(`:56-61`), PASS #2·#3(`:65-66`)
- **문제**: 인라인된 *상수값은 맞지만*, 그 값들로 U(s,t)를 실제로 계산하는 데 필요한 **정의가 빠져 있어** 이 문서만으로는 utility를 정확히 구현할 수 없다. 동시에 "research/03 참조 금지, 자립"(`:56`)이라 해놓고 PASS #3(`:66`)은 **research/03의 12시점 표**를 기대값 출처로 참조 → **내부 모순**.
  1. **`t_sepsis` 유도 규칙 누락 (치명적)**: 공식은 `t_sepsis = argmax(SepsisLabel) − dt_optimal` 즉 **첫 양성 라벨 인덱스 + 6h**다 [확인됨: §1차 확인]. 핸드오프는 창을 "발병(onset) 기준"이라고만 하고, SepsisLabel에서 onset을 어떻게 뽑는지 안 적었다. 구현자가 `t_sepsis = 첫 양성 인덱스`로 잡으면 **모든 보상/벌점 창이 6h 어긋나** 평가 전체가 거짓이 된다(라벨 shift를 다시 빼야 함).
  2. **`best_predictions` 범위 미인라인**: `:61`은 "best_predictions(완벽 예측 정책의 최적)"이라 순환 서술. 공식 정의는 `1 over [max(0, t_sepsis+dt_early) : min(t_sepsis+dt_late+1, n)]` [확인됨]. PASS #2(best→1.0)가 이 정의에 의존하는데 정의가 없다.
  3. **piecewise 경계·절편 미인라인**: slope(m1/m2/m3)만 있고, (a) TP 너무이른 구간의 `max(…, u_fp)` 바닥 클리핑, (b) FN이 `t_sepsis+dt_optimal` 이전엔 0이라는 시작점, (c) 각 직선의 절편이 없다. slope만으로는 U(s,t)를 일의적으로 못 만든다.
- **근거**: `h2_handoff:56,61,66`; 공식 `evaluate_sepsis_score.py`(t_sepsis·best_predictions·max-clip — §1차 확인); research/03:96-118(표 실재).
- **제안**:
  - utility 정의를 **완전 자립형으로 인라인**: ① `t_sepsis = argmax(label) − dt_optimal`, ② best_predictions 슬라이스 범위, ③ 세 구간 직선의 절편 + TP 바닥 클리핑 + FN 시작점, ④ no-sepsis는 pred=1→`u_fp`, pred=0→`u_tn`, ⑤ `t > t_sepsis+dt_late`는 창 밖.
  - PASS #3의 **기대 12행 값을 핸드오프 안에 표로 직접 인라인**(research/03 96-118 전사: −12h행 TP=−0.05/FN=0 … −6h TP=+1.00 … +3h TP=0/FN=−2.00). 그래야 research/03 미개봉으로 기계판정 가능.
  - `:56`의 "research/03 참조 금지"와 `:66`을 일치시킴(인라인하면 참조 불필요).

### HOLD-2 — HP 탐색(objective=utility) ↔ τ 선정(utility 최대) **중첩 순서 미명세**

- **항목**: H2-b(`:81-82`), H2-c(`:109-110`)
- **문제**: HP 선택 기준이 "A-val utility"(`:81,109`)인데 utility는 확률→라벨 변환에 τ가 필요하다(`:82,110`). 즉 **각 HP 후보의 utility를 구하려면 τ가 먼저 필요**한데, τ와 HP의 결합 순서가 없다. 가능한 해석이 갈린다:
  - (가) 각 HP trial마다 τ를 utility-최대화로 잡아 그 *max-over-τ utility*를 trial 점수로 → best HP의 (HP*, τ*) 동시 동결. *(올바른 해석)*
  - (나) 탐색 중엔 고정 τ(예 0.5)로 HP만 고르고, 동결 후 τ만 최적화. → (가)와 **다른 HP가 선택될 수 있음**.
  핸드오프가 (가)를 명시하지 않으면 구현자마다 다른 선택 절차가 나와 "결정이 의도대로"가 무너진다(누수는 아님 — 전부 A-val 내부).
- **근거**: `h2_handoff:81,82,109,110`; 결정 4 τ 정책 / 결정 6 objective.
- **제안**: H2-b·H2-c에 한 줄 — "각 HP trial 점수 = **τ에 대해 최대화한 A-val utility**, 최종은 최고 trial의 (HP\*, τ\*)를 **함께 동결**." (탐색 비용을 줄이려면 trial 중 τ는 coarse grid로, 동결 후 fine grid 재선정도 가능 — 택1 명시.)

### HOLD-3 — 실패 모드 **정지 트리거 누락** (게이트 미작동 구간)

- **항목**: 실패 모드(`:150-156`)
- **문제**: [E]가 짚은 정지 트리거 일부가 빠져, 그 상태에서 런이 **조용히 계속**된다.
  1. **MLflow 기록 실패** — 아티팩트·지표가 MLflow에 의존(H2-d 집계·H3 재현)하는데 기록 실패 시 정지 트리거 없음. 트레이스 없이 d에서 빈 표가 됨.
  2. **OOM / 긴 시퀀스(최대 331h) 메모리** — 결정 5가 "풀 규모에서 확인"으로 남긴 항목인데(`h2_decisions:91`) 실패모드에 메모리 정지 트리거 없음.
  3. **트리 trial 발산/비유한 점수** — GRU loss 비유한은 잡지만(`:154`,PASS `:119`), **트리 trial이 NaN/degenerate utility**를 내는 경우 정지 트리거 없음.
- **근거**: `h2_handoff:150-156`(현 목록), `:85,155`(MLflow 의존), `h2_decisions:91`(긴 시퀀스 미결).
- **제안**: 실패 모드에 추가 — "MLflow write 실패 → 정지", "OOM/메모리 초과 → 정지(+길이 버킷팅 권고)", "임의 trial의 A-val utility/PR-AUC 비유한 → 그 trial 폐기 또는 정지". 각각 PASS/로그에 연동.

---

## 1차 확인 결과 (utility 인라인값·12시점 표)

`physionetchallenges/evaluation-2019` `evaluate_sepsis_score.py` raw 직접 대조:

| 항목 | 공식 코드 | 핸드오프 인라인(`:57-59`) | 판정 |
|---|---|---|---|
| `dt_early/optimal/late` | −12 / −6 / +3 | −12 / −6 / +3 | ✅ |
| `max_u_tp` / `min_u_fn` | +1 / −2 | +1 / −2 | ✅ |
| **`u_fp` / `u_tn`** | **−0.05 / 0** | **−0.05 / 0** | ✅ (명시 대조함) |
| `m1/m2/m3` | 1/6 / −1/9 / −2/9 | +1/6 / −1/9 / −2/9 | ✅ |
| **`t_sepsis` 유도** | `argmax(labels) − dt_optimal` | **없음** | ⛔ HOLD-1 |
| **`best_predictions`** | `1 @ [max(0,t_sepsis+dt_early) : min(t_sepsis+dt_late+1, n)]` | "정책의 최적"(순환) | ⛔ HOLD-1 |
| 정규화식 | `(obs−inaction)/(best−inaction)` | 동일(`:61`) | ✅ |

→ **인라인 상수값은 전부 [확인됨] 정당.** "값이 틀려 평가가 거짓"은 아님. 단 **유도규칙·best정의·piecewise가 빠져 구현 불완전**(HOLD-1).

**12시점 표**: research/03:96-118에 **실재**(패혈증 환자 TP/FN 두 열). 단 표는 경계행 포함 **14행**(검증은 line 180 "12개 시점")이라 핸드오프의 "12시점" 명칭은 부정확 → 행수 정정 + 값 인라인(HOLD-1). 정규화 정의상 **전부음성==0.0**(=inaction), **best==1.0**은 수학적으로 정확 → PASS #1·#2 등식은 타당(단 #2는 best정의 필요).

---

## 실행 전 권고 (비차단)

1. **동적 B-guard 기법 명시** — PASS "#4 B 미접촉(정적·동적)"은 가장 중요한 누수 게이트인데 *기법*이 없음. 구체화: 정적=H2 스크립트에서 B split 인덱싱 grep 0건, 동적=`assert set(loaded_pids) & set(B_pids) == ∅`(학습·튜닝·τ·정규화 입력 전부). H1-b 누수 방어선 선례 재사용.
2. **아티팩트 native 포맷 명시** — `:85,155`가 "전처리통계 아티팩트"라 하나 포맷 미지정. H3 B 재현 위해 XGBoost `.ubj/.json`, LightGBM `.txt`(`Booster.save_model`), GRU `state_dict`, 전처리통계(μ/σ·fill mean·pos_weight·clip)·τ는 npz/json로 — H3가 로드 가능한 형식임을 못박기.
3. **길이 버킷팅** — 331h 포함 가변 길이를 batch-max로 패딩하면 메모리·연산 낭비. 길이 버킷 배칭 권장(정확성 무관, HOLD-3 OOM과 연동).
4. **`scale_pos_weight` 고정의 근거 1줄** — `:81,94`에서 고정으로 정리됐으나, GRU pos_weight도 동일하게 A-train 산출 고정임을 H2-c에 명시(일관).
5. **H2-a 출력 인터페이스 [E]** — `utility(label_seq, pred_label_seq) -> U_norm`이 b·c(선택·τ)와 d(MLflow 집계)에 동일 시그니처로 물리는지 한 줄. 트리는 per-timestep 예측을 **환자별 재조립** 후 호출(`:60` 취지)임을 b에도 반복 명시.

---

## 다음 단계

**HOLD 3건(utility 인라인 완전성·HP↔τ 순서·실패모드) 수정 후 재검토.** 전부 PASS 전 구현(코드·디렉토리 생성) 진입 금지(WORKFLOW §5·§6).
