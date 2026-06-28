# 핸드오프 검토 — H3 (레드팀, 실행 명세 검토)

- **대상**: `design/h3_handoff.md` (초안)
- **대상 commit**: `e939a64`
- **검토일**: 2026-06-28
- **핵심 질문**: B를 개봉해 채점할 때 **누수가 코드로 차단**되고, **공식 동등성·마스크 비교가 프로그래매틱**한가.
- **판정**: ⛔ **HOLD 1건 → 구현 금지.** 누수 가드(frozen-only + grep + bit-동일)·공식 동등성·아티팩트 충분성은 실행 가능하게 명세됨. 막히는 곳은 **H3-c 마스크 채널 *구성 순서*가 미명세** — 틀리면 H3의 정의상 산출물(마스크 판정)이 **소리 없이 거짓**이 된다.

---

## PASS (실행 가능 확인)

- **§0 B 누수 규칙 [A]** — frozen-only 격리 `score_frozen(model, frozen_stats, frozen_tau, X_B)`(`h3_handoff:20`) + 금지함수(fit·tune·select·정규화통계) 정적 grep + "정규화·τ가 A 동결값과 **bit-동일**"(PASS #3, `:57`)로 재계산/재선정 경로 차단. **핵심 누수(B 재정규화·τ 재선정)는 차단됨.** *(동적 assert 문구는 모호 — 권고 1.)*
- **A-val 인용 [A]** — "h2_results.md에서 그대로 인용, 재계산 금지"(`:51`), PASS #4가 일치 강제. (정밀도는 권고 4.)
- **공식 동등성 [B]** — B 개봉 *전*(`:44-47`), A-val/합성 입력(B 미사용), 엣지(코호트 합산·정상환자·짧은 시퀀스)로 H2-a 14행 위에 **공식 코드 자체와 끝단 동등** 추가. 우리 `patient_utility(labels, preds)`가 배열 API라 공식 `compute_prediction_utility`와 직접 대조 가능(아래 §실현성). 실현 가능. PASS.
- **B 미개봉(동등성 단계)** — A-val/합성만(`:45`). PASS.
- **마스크 필수·전이성·통제 [C]** — H3-c 필수(최소 GRU vitals, `:69`), 판정=ON/OFF의 A→B gap 비교(전이성, `:74`), 통제=HP\*·seed 동일·입력채널만 차이·τ 각자 A-val(`:72`). DDD 정합. PASS *(단 채널 구성 순서 = HOLD).*
- **정합/실현성 [D]** — 6조합 MLflow 로드 가능(run명 `h2b-{model}-{fs}`/`h2c-gru-{fs}`, 아티팩트 `model/`·`preprocess/`·`preprocess.json` — H2 코드 확인). GRU masked PR-AUC B에서 패딩 제외(`gru.evaluate`), PASS #5가 masked≠unmasked 강제. PASS *(로드 경로 명시는 권고 2).*
- **실패 모드** — 공식 불일치·B 유입·A-val 재계산·패딩 미제외·마스크 통제 위반·MLflow 로드 실패·OOM·비유한 전부 정지 트리거(`:93-99`). 포괄적. PASS.

---

## HOLD (수정 필요)

### HOLD-1 — H3-c 마스크 채널 **구성 순서·결합 지점 미명세** (H3 산출물 무력화 위험)

- **항목**: H3-c 구현(`h3_handoff:72`), PASS #1(`:78`)
- **문제**: 핸드오프는 "GRU 입력 채널 F→2F(마스크 채널 추가, H1 `missing.py` opt-in)"(`:72`)라고만 한다. 마스크가 **유효하려면** 다음이 필수인데 **하나도 인라인되어 있지 않다**:
  1. **마스크는 RAW NaN에서, ffill *이전*에 생성**해야 한다 [확인됨: `missing.py:4-5,18` "Built from RAW NaN positions (**must precede ffill**)", H1 결정 8]. **ffill *후*에 만들면 전부 관측(=all-ones)** → 마스크 채널이 정보 0 → 마스크 ON ≈ OFF + 잡음 → **"마스크 효과 없음"이라는 거짓 결론**. 그런데 이건 에러 없이 그럴듯하게 나와 **소리 없이 H3 판정(마스크를 켤까?)을 망친다.**
  2. **결합 지점**: 마스크(0/1)는 **정규화 *후*에 concat**하고 **z-score 대상이 아니다**(0/1 유지). 정규화 파이프라인에 통과시키면 마스크가 왜곡됨.
  3. 순서 종합: `mask = missing_mask(raw_slice)` → 별도 보관 → 피처는 ffill→fill→clip→z-score → `X = concat([norm_feats(F), mask(F)], axis=-1)` → `input_dim=2F`.
- **근거**: `h3_handoff:72`(순서 없음); `missing.py:4-5,18`(mask는 ffill 전·RAW); H1 결정 8. WORKFLOW §6(자립형 — 외부 파일 docstring 의존 금지) + §0(틀린 전제가 검증 없이 흘러감).
- **제안**: H3-c 구현에 위 1~3을 **인라인**(코드 순서 명시). PASS #1에 "마스크가 ffill 이전·RAW에서 생성됨(=all-ones 아님: 마스크 채널 평균이 관측률과 일치, 0/1 분포 확인)"을 **assert로 추가** — all-ones 붕괴를 게이트가 잡도록.

---

## 실행 전 권고 (비차단)

1. **§0 동적 assert 문구 정정** — "B_pids ∩ 입력_pids 관련 분기 차단"(`:20`)은 H3에 안 맞다(B가 *채점 입력*이라 교집합은 당연히 비지 않음). 실제 작동 가드는 **(a) 정적 grep: `crosssite.py`/h3 스크립트가 `compute_norm_stats`·`compute_fill_mean`·`select_threshold`를 호출하지 않음, (b) bit-동일: B 변환에 쓰인 μ/σ·fill·τ가 아티팩트 로드값과 `array_equal`**. 이 둘로 문구를 교체(교집합 표현 삭제).
2. **아티팩트 로드 경로 인라인(자립성)** — 실제 로드법 명시: `mlflow` 실험 `h2`, run명 `h2b-{model}-{fs}`/`h2c-gru-{fs}`, 다운로드 `model/{...}.{ubj|txt|pt}`·`preprocess/pre_{fs}.npz`·`preprocess.json`. GRU는 `json{hp,input_dim}`로 `GRUm2m` 재구성 후 `load_state_dict`+`.eval()`(드롭아웃 off), 트리는 `Booster.load_model`. **B 채점은 `gru.evaluate` 재사용**(eval-mode+masking 보장).
3. **공식 스크립트 입수·대조법 명시** — `physionetchallenges/evaluation-2019`의 `evaluate_sepsis_score.py`. 파일 브리지 대신 **공식 `compute_prediction_utility(labels, preds)`를 import해 우리 `patient_utility`와 환자별 + 코호트 정규화로 직접 비교**(±tol, **tol 수치 명시 예: 1e-6**).
4. **A-val 정밀도** — gap엔 `h2_results.md`(4자리 반올림) 대신 **MLflow 원값** 사용, "MLflow값이 h2_results.md로 반올림되면 일치"를 PASS #4 일관성 체크로.
5. **B 이상 라벨 환자(있다면) 채점 포함/제외 1줄** — H1-a 로깅 기준.
6. **gap 부트스트랩 CI**(`:52` "여유 시")를 사람 체크포인트 자료로 권장 — 작은 gap·순위역전 과해석 방지.

---

## 다음 단계

**HOLD-1(마스크 채널 구성 순서·결합) 해소 후 재검토.** 전부 PASS 전 구현(코드·디렉토리 생성) 금지(WORKFLOW §5·§6).
