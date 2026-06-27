# 핸드오프 검토 — H1 (레드팀, 실행 명세 검토)

- **검토일**: 2026-06-27
- **대상**: working tree `design/h1_handoff.md` (초안), base HEAD `9e93fd8`.
- **검토자**: Claude Code (레드팀 모드 — 동의가 아니라 구멍 찾기)
- **핵심 질문**: "이 문서만으로 결정이 의도대로 구현되고 게이트가 실제 작동하는가."
- **결론**: **HOLD 2건.** 자립성·결정정합·헤더 일치는 대체로 양호하나, **자동 진행 토막의 PASS assert 중 1건이 코드로 참/거짓이 안 떨어지고(0-fill 검증), 부속결정이 H1 범위로 박은 pos_weight 입력 산출이 핸드오프에 누락**됐다. 워크플로우 §5에 따라 **HOLD 해소 전 구현(코드 작성) 금지.**

> 1차 확인: PhysioNet 2019 실제 `.psv` 헤더(`data/raw/training_setA/p000001.psv`) 직접 대조. 레포 대조: `h1_decisions.md`(v4), `reports/eda_findings.md`, `smoke/`(data.py·dataset.py·model.py·train_smoke.py).

---

## PASS (실행 가능 확인)

### [C] 자립성 / 컬럼 정합 — ✅
- **PhysioNet 헤더 1차 대조 [확인됨]**: 실제 `.psv` 헤더 =
  `HR|O2Sat|Temp|SBP|MAP|DBP|Resp|EtCO2|BaseExcess|HCO3|FiO2|pH|PaCO2|SaO2|AST|BUN|Alkalinephos|Calcium|Chloride|Creatinine|Bilirubin_direct|Glucose|Lactate|Magnesium|Phosphate|Potassium|Bilirubin_total|TroponinI|Hct|Hgb|PTT|WBC|Fibrinogen|Platelets|Age|Gender|Unit1|Unit2|HospAdmTime|ICULOS|SepsisLabel`.
  - 활력 7 + EtCO2(`:20,24`) 철자·존재 일치. 검사 9종(`WBC,BUN,Platelets,Lactate,Creatinine,Glucose,PTT,HCO3,Calcium`, `:22`) **전부 정확한 철자로 실재**. 제외 4종(`ICULOS,Unit1,Unit2,HospAdmTime`, `:25`) 실재·제외 타당. `SepsisLabel`·`Age`·`Gender` 일치. ✅
- **외부 레포 미참조**: 본문이 self-contained, in-repo 파일만 참조(`:13`). pdm-mlops 등 의존 없음. ✅

### [D] 결정-구현 정합 — ✅(핵심 항목)
- **런타임 순서 = 결정 8**: 핸드오프 GRU 경로 `마스크[원본 NaN] → ffill → train평균 fill`(`:95`) → 클리핑→z-score(`:96`) → 시퀀스(`:97-99`). **마스크가 ffill 앞** — `h1_decisions.md:141`과 정확히 일치. ✅
- **단방향·우측패딩·validity mask 양쪽 제외 = 결정 4**: `:99-101`(우측 패딩 + validity mask + 학습·평가 양쪽 제외), `:100,111`(bidirectional=False). `h1_decisions.md:79`·핸드오프 강제 3종과 일치. ✅
- **피처 수 = 결정 1**: 캐시 19(=활력7+EtCO2+인구2+검사9, `:24`), 활력셋 9(=7+2), 활력+검사셋 18(=9+9) (`:26`). `h1_decisions.md:38`과 일치. ✅
- **타깃 봉인 = 결정 5**: cross_site에서 B를 train·val·정규화·pos_weight 미사용(`:92`), 평가만. ✅

### [B] 자동/체크포인트 구조 — ✅(대체로)
- a→b 자동, H1-c·재스모크 ⏸체크포인트(`:33-39`). **H1-c 체크포인트는 정당** — "측정밀도 누수 실재 → 마스크 OFF 정당화"는 진짜 사람 판단이고 프로그래매틱 부분(파일 생성+수치 제시, `:131-132`)과 분리됨. ✅ 재스모크는 자동 실행 후 결과를 사람이 확인(`:35,154`) — H2 진입 전 사인오프로 합리적, 불필요한 정지 아님. ✅

### [E] 토막 인터페이스 — ✅(대체로)
- H1-a 출력(환자별 가변길이 T×19 + 라벨 + site + pid, `:71`) → H1-b 입력 맞물림, 가변 길이 보존이 시퀀스 구성(`:99`)까지 이어짐. ✅
- 실패 모드 목록(`:164-169`)이 주요 정지 트리거(환자수·제외컬럼·0-fill·환자누수·B유입·정규화경계·마스크순서·bidirectional·평가패딩) 포괄. ✅ (누락 2건은 권고로.)

---

## HOLD (수정 필요)

### HOLD 1 — H1-b PASS#6 "0-fill 없음"이 코드로 판정 불가/오정의
- **항목**: `h1_handoff.md:110` — "**0-fill 없음**: GRU 입력에 결측 기인 0이 없음(평균/ffill로만 채워짐)."
- **문제**: GRU 입력은 **클리핑→z-score 정규화 후**(`:96`)다. mean-fill된 결측 위치는 train 평균으로 채워지므로 **z-score = (mean−mean)/std = 정확히 0**이 된다. 게다가 평균과 같은 *정상* 측정값도 z-score 0이다. 즉 정규화된 GRU 입력엔 **0이 정상적으로 다수 존재**하고, "결측 기인 0"과 "정상값 0"을 사후에 구분할 수 없다. 따라서 이 assert는 *항상 실패하거나(0이 있으니) 무의미*해 **참/거짓이 안 떨어진다.** 자동 진행(a→b) 게이트인데 게이트가 작동 안 함.
- **근거**: `:96`(정규화 단계), `:110`(assert 문구), `smoke/data.py:88-95`(mean-fill 후 z-score = mean 위치가 0이 됨).
- **제안(측정 가능 형태)**: 0-fill 금지는 **결측 대치 단계(raw 공간)에서** 검증한다 —
  1. 대치 직후(정규화 *전*) `assert not np.isnan(X).any()` (스모크의 실제 체크, `smoke/train_smoke.py:75`),
  2. 대치된 위치의 raw 값이 **train 평균 또는 ffill 값과 일치**(literal 0이 아님)임을 assert,
  3. (문구 정정) "정규화 후 0 부재"가 아니라 "결측을 0으로 치환하지 않음"으로.

### HOLD 2 — pos_weight 입력 산출(부속결정 H1 범위)이 핸드오프에 누락
- **항목**: H1-b 구현·PASS, 재스모크 어디에도 **클래스 비율 집계(pos_weight 입력)** 단계가 없음.
- **문제**: `h1_decisions.md:153`(부속결정)이 "**pos_weight … 비율은 A-train에서 산출. H1은 산출 입력(클래스 비율 집계)까지**"로 **H1 범위에 명시**했다. 그러나 핸드오프 H1-b(`:91-112`)와 재스모크(`:144-151`)에 per-timestep 양성비율(=pos_weight 입력)을 **A-train에서 산출·로깅하는 단계가 없다.** 결정이 H1 산출물로 지정한 것을 실행 명세가 빠뜨림(결정-구현 불일치).
- **근거**: `h1_decisions.md:153` vs `h1_handoff.md:91-112,144-151`. (스모크는 윈도우 기준 `pos_weight≈59.7`을 산출했으나 m2m은 **시점 기준 ≈54.6** `eda_findings.md:75`로 재산출돼야 함 — 그 산출 단계 자체가 없음.)
- **제안**: H1-b에 "**A-train per-timestep 양성:음성 비율 집계 → pos_weight 입력**" 단계 추가, PASS에 "비율이 A-train에서만 산출(B·val 미포함)" + 산출값 로깅 assert. (적용은 H2이므로 산출·기록까지만.)

---

## 실행 전 권고 (비차단)

1. **H1-a PASS#4 "≈/일치" 비크리스프**(`:78`): "결측률이 EDA와 일치(예: WBC ≈93.6%)"는 자동 토막인데 허용오차가 없어 "대충 맞으면 통과"식. 캐시는 raw 동일본이므로 **EDA 수치와 정확 일치해야** 함 → `assert abs(cache_miss% − eda_value) < 0.01` 같은 **명시 허용오차**로.
2. **H1-a PASS#5 라벨 블록**(`:79`): "위반 환자 수 로깅"은 assert가 아님(로깅은 정지 안 함). EDA가 양성블록 연속·끝종료를 **100%**로 측정(`eda_findings.md:81`)했으니 **`assert 위반==0`**(하드)로 올리거나, 비-하드면 PASS 기준에서 빼고 "진단 로그"로 명시.
3. **H1-b PASS#3 정규화 재현**(`:107`): 절차를 식으로 — `assert used_mean == recompute(A-train)`(fp 허용오차) **그리고** `assert used_mean != recompute(A-val)`. "재현 검증/불일치 확인" 서술만으론 구현자 해석 여지.
4. **H1-b PASS#7 bidirectional 체크 위치**(`:111`): H1-b는 **데이터 변환** 토막이라 GRU 모델 인스턴스가 없을 수 있음 — `bidirectional=False` assert는 GRU를 띄우는 **재스모크/`config.py`**에 두는 게 자연(`:151`과 중복). H1-b엔 "config 상수 bidirectional=False"로 둘 것.
5. **재스모크 PASS#2 "loss 하락"**(`:149`): 작은 스모크에서 단조 하락은 **불안정**(시드·스텝 의존). 스모크 원본 기준은 "loss **유한**"(`smoke/train_smoke.py:148`). **유한을 하드 게이트로, 하락은 로깅**(기대치)으로.
6. **마스크 극성 명시**: H1-b#4가 "마스크 1 = 원본 NaN 위치"(`:108`) = **1=결측**. 결정 7/8의 informative-missingness 마스크와 sequence의 **validity mask(1=실측)**(`:99`)는 **극성이 반대**다. 두 마스크 이름·극성을 핸드오프에 못박아 혼동 차단(`missingness_mask` 1=결측 / `validity_mask` 1=실측).
7. **트리 요약 NaN 처리**(`:102`): "전부 NaN → NaN 유지"만 있고 **부분 NaN lookback의 집계 규칙**(nan-aware: nanmean/nanmax 등 결측 skip)이 미정의. 명시 권장.
8. **피처셋 파생 assert 부재**: 결정 1 검토요청("활력셋9 ⊂ 활력+검사셋18, EtCO2 모델입력 제외")에 대응하는 assert가 H1-b에 없음 — **활력셋 컬럼=={7활력+2인구}, 활력+검사셋=={+9검사}, 두 셋 모두 EtCO2 불포함**을 assert 추가.
9. **실패 모드 누락 2건**(`:164-169`): (a) **긴 시퀀스 메모리**(최대 331h — 실제 위험 낮으나 `pack_padded_sequence`로 패딩을 forward에서 배제 명시), (b) **전부-NaN 컬럼/유령 시점**(Age·Gender는 0% 결측이라 환자 전체-NaN은 불가하나, 특정 검사 컬럼 환자내 전부-NaN은 mean-fill로 처리됨을 *확인*으로 명시). 부속결정 "유령 시점 로깅"(`h1_decisions.md:154`)과 연결.

---

## 판정 / 다음 액션
- **HOLD 2건(0-fill 검증 / pos_weight 입력 산출)**을 `h1_handoff.md`에서 해소 후 재검토. HOLD 잔존 상태로 **코드·디렉토리 생성 금지.**
- 권고 1~9는 HOLD 정정 시 함께 반영(특히 1·3·5는 자동 게이트의 크리스프성 직결).
- HOLD 해소 + 권고 반영 후 전 항목 PASS면 구현(H1-a부터) 진입.
