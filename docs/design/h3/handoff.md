# H3 구현 핸드오프 — cross-site 평가 (B 개봉)

> **설계 근거**: [`docs/design/h3/decisions.md`](decisions.md)(v2, 검토 PASS `8a7887f`). 실행 명세로 번역.
> **워크플로우**: [`WORKFLOW.md`](../WORKFLOW.md). 자립형이며, 검토(`docs/design/h3/handoff_review.md`) 통과 후 실행.
> **개정 이력**
> - **v2 (2026-06-28)** — 핸드오프 검토 `66f1a0f`의 HOLD 1건 + 비차단 3건 반영
>   - HOLD: H3-c **마스크 채널 구성 순서 인라인**(mask=RAW NaN·ffill 이전 생성 → 피처 정규화 → concat, 마스크는 z-score 제외) + PASS에 **all-ones 붕괴 assert**(채널 평균==관측률). ffill 후 생성하면 전부 1이 되어 "마스크 효과 없음" 거짓 결론 → H3 산출물 무력화 방지.
>   - §0 동적 가드 문구 정정: "B_pids∩입력"(B는 채점 입력이라 부적합) → **fit/select 미호출(grep) + A 동결값 bit-동일**.
>   - 아티팩트 로드 경로 인라인 + 공식 스크립트 repo·tol 명시.
> - v1 (2026-06-28) — 초안. DDD nit 흡수: 우승점수는 스케일 참조만(분해/재현 안 함), 사람 체크포인트에 "B는 관찰·결정은 A-val+H4" 명시.

---

## 0. 공통 규칙 (자립형)

### 환경 / 입력
- 기존 `pyproject.toml` 환경, CPU. 외부 레포 참조 금지.
- 입력 = H2 산출물: **6조합 학습 아티팩트**(MLflow) — GRU(state_dict + npz{μ/σ·fill·clip} + json{hp·input_dim·τ}), 트리(.ubj/.txt + json{τ·전처리}). H1 `src/sepsis/data/`, `eval/utility.py`·`eval/threshold.py`.
- **B 데이터**: H1 캐시의 setB(20,000명, 봉인). H3에서 처음 개봉.

### ★ B 누수 규칙 (H3 핵심 — 결정 2)
- B 채점은 **A에서 동결한 아티팩트만** 사용: 정규화 μ/σ, fill 평균, 클리핑 범위, **τ** 전부 H2 저장값. **B로 어떤 재계산·재튜닝·재선정도 금지**.
- **B는 채점 함수에만 진입**. 학습·튜닝·선정·전처리통계 산출 함수엔 B가 **전달되지 않음**.
- **강제 기법**: 채점은 `score_frozen(model, frozen_stats, frozen_tau, X_B)` 형태 frozen-only 함수로 격리. **정적(grep)**: 채점 경로에서 `fit`·`tune`·`select`·정규화통계 산출 함수가 호출되지 않음. **동적**: 채점에 쓰인 정규화 μ/σ·fill·clip·τ가 **A 동결 아티팩트와 bit-동일**함을 assert(B로 재계산·재선정 안 됨). *(B는 채점 입력으로 들어오는 게 정상이므로 "B_pids 차단"이 아니라 "B로 학습/선정/재정규화 안 함"을 검증.)*
- **아티팩트 로드(인라인)**: MLflow run(H2-b·c)에서 GRU=`load_state_dict`+`.eval()`, 트리=native(.ubj/.txt) load. 전처리(μ/σ·fill·clip)·τ는 동반 npz/json에서. 채점은 H2 `gru.evaluate`(masked PR-AUC)·트리 평가 재사용.
- **피처셋/모델 선택에 B 미사용**: B 점수는 관찰만, 선택은 A-val(+H4).

### 진행 규칙
- 각 토막 완료 시 commit & push. PASS는 프로그래매틱, 실패 시 그 자리 정지·보고.
- **자동 진행**: H3-a(채점 전 공식 대조)는 H3-b 안의 첫 스텝.
- **사람 체크포인트** ⏸: H3-b 종료(gap·순위역전 해석), H3-c 종료(마스크 OFF 최종 확정). 둘 다 자동 진행 아님.
- 첫 B 개봉이므로 **각 토막 결과 보고 후 멈춤**.

### 디렉토리 (생성)
```
src/sepsis/eval/
  official_compat.py   # H3-b 스텝1: 공식 evaluate_sepsis_score.py 동등성
  crosssite.py         # H3-b: A 동결 아티팩트로 B 채점(frozen-only)
scripts/
  h3b_crosssite.py · h3c_mask_check.py
docs/reports/
  h3_results.md        # gap 표·해석
```

---

## H3-b — cross-site 채점 (B 개봉 ★) (결정 1·2·3·4)

### 스텝 1 — 공식 utility 동등성 (B 개봉 전, 결정 4)
- 공식 `evaluate_sepsis_score.py` 입수(repo: `physionetchallenges/evaluation-2019`, 공개). 우리 `eval/utility.py`와 **동일 입력 → 동일 출력(tol 1e-6)** 대조. 입력은 A-val 또는 합성(여러 환자·정상 섞임·짧은 시퀀스 등 엣지 포함) — **B 미사용**.
- 목적: H2-a 14행(단일 환자 정의) 위에, **코호트 합산·정상환자·엣지 케이스 수준**에서 공식 구현과 동등 확인. 우승 ~0.36은 **스케일 참조로만 언급**(분해·재현 안 함).
- 통과해야 스텝 2 진행.

### 스텝 2 — 6조합 A→B 채점
- 6조합(XGB·LGBM·GRU × vitals·vitals_labs) 각각: H2 저장 모델 + **A 동결 전처리·τ**로 B를 변환·채점. GRU는 masked PR-AUC(B에서도 패딩 제외).
- 지표: PR-AUC + utility. **A-val 점수는 docs/reports/h2_results.md에서 그대로 인용**(재계산 금지).
- `docs/reports/h3_results.md`: A-val · B · **gap(=A_val−B)** 나란히, A/B 각 순위. (gap 부트스트랩 CI는 여유 시.)

### PASS 기준 (assert)
1. 공식 동등성: 우리 utility == 공식 스크립트 출력 (±tol), 엣지 케이스 포함.
2. 6조합 B 채점 무오류 완주, PR-AUC+utility 기록.
3. **B 누수 가드**: 채점이 frozen-only 함수로 격리, B가 fit·tune·select·전처리통계 산출에 미진입(정적 grep + 동적 assert). 정규화·τ가 A 동결값과 bit-동일.
4. gap 표 생성, A-val 점수가 h2_results.md와 일치(재계산 아님).
5. GRU masked PR-AUC가 B에서 패딩 제외(masked≠unmasked 확인).

### 진행
- PASS → ⏸ **사람 체크포인트**: gap·순위역전 보고. **B는 관찰만 — 피처셋/모델 선택은 A-val+H4에서 하며 B 점수로 고르지 않는다.** 해석 후 H3-c.

---

## H3-c — 마스크 누수 검증 (결정 5) ⏸ 사람 체크포인트

### 범위
마스크 OFF(H2 기존) vs **마스크 ON**(재학습)을 A→B gap으로 비교. WORKFLOW §8·H1 결정 7의 "마스크 채택은 H3 A·B 검증과 함께" 약속 이행. **최소 대표 1조합 = GRU vitals 필수.**

### 구현
- 마스크 ON 재학습: GRU 입력 채널 F→2F(마스크 채널 추가, H1 `missing.py` opt-in).
  - **★ 마스크 채널 구성 순서(반드시 인라인 준수 — 틀리면 H3 산출물 무력화)**:
    1. **`mask = missing_mask(raw)` — RAW NaN 위치에서, ffill *이전에* 생성**(1=관측, 0=결측). *ffill 후 만들면 결측이 다 채워져 전부 1(all-ones) → 마스크 정보 0 → ON≈OFF → "마스크 효과 없음" 거짓 결론. 에러 없이 조용히 망친다.* [H1 결정 8 / missing.py "must precede ffill"]
    2. 피처는 따로: `ffill → fill(train평균) → clip → z-score`.
    3. **`concat([정규화된_피처, mask])`** → input_dim=2F. **마스크는 z-score 대상 아님**(0/1 그대로, 정규화 후 결합).
  - **공정 통제**: OFF와 **HP\*·seed 동일, 입력 채널만 차이**, τ는 각자 A-val에서, 그 외 전처리 동일.
- ON/OFF 둘 다 A-val·B 채점 → 각 gap 산출.
- 판정: **ON/OFF의 A-val→B gap 비교(전이성)**. ON이 A-val은 비슷/우세인데 **B에서 더 무너지면(gap↑)** → site-specific 측정패턴 학습 → **OFF 정당**. gap 차이 미미 → OFF 영향 없음 확정.
- `docs/reports/h3_results.md`에 ON/OFF×(A-val,B,gap) 표 + 해석.

### PASS 기준 (assert)
1. GRU vitals 마스크 ON 재학습 완주(input_dim 2F 확인), 아티팩트 저장.
2. **마스크 채널 무결성**: 마스크가 RAW·ffill 이전 생성 → **채널 평균 == 관측률**(=1−결측률, all-ones 아님). 1.0이면 ffill 후 생성(붕괴)로 판정·정지. 마스크는 정규화 안 됨(값 ∈ {0,1}).
3. **공정 통제**: ON/OFF가 HP·seed 동일, 입력 채널만 차이(코드/설정 확인).
4. ON/OFF × (A-val, B, gap) 수치 산출.
5. B 누수 가드(H3-b와 동일): ON 학습에 B 미사용, 채점만.

### 진행
- ⏸ **사람 체크포인트**: gap 비교 보고 → 사람이 **마스크 OFF 최종 확정**(WORKFLOW §8 귀결). 통과 시 H3 종료 → H4(운영, 최우선).

---

## 범위 외 (H4)
- 서빙·스트리밍 시뮬레이터, 드리프트 감시, 재학습 (H4, 최우선)
- 피처셋 최종 확정 (A-val+H4 운영 신호, B 미사용)
- 양방향 B→A (보류)

## 실패 모드 (정지 트리거)
- 공식 동등성 불일치(±tol 초과)
- B가 fit·tune·select·전처리통계 산출에 유입 / 정규화·τ가 A 동결값과 불일치
- A-val 점수를 B 채점 중 재계산(h2_results.md와 어긋남)
- GRU 평가 패딩 미제외 / 마스크 ON 재학습이 OFF와 입력채널 외 다름
- MLflow 로드 실패 / OOM(긴 시퀀스) / 비유한 점수
- 위 중 하나라도 → 정지·보고.

## 검토 요청 (docs/design/h3/handoff_review.md 용)
- B 누수 가드(frozen-only 격리 + grep/assert)가 실제로 강제되는지 — 최중요.
- 공식 스크립트 입수·동등성 판정이 프로그래매틱하고 B 미개봉인지.
- 마스크 ON/OFF 공정 통제(입력채널만 차이)가 코드로 보장되는지.
- A-val 점수 인용이 재계산 아닌지, gap 표가 일관되는지.