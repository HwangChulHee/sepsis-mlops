# review.md — 부하테스트 (나) DDD 경량 검토

- **대상**: `docs/design/load-test/decisions.md` (설계부)
- **핵심 질문**: [확인됨] 태그가 src/ 실물과 맞나 / 시나리오가 상태·측정을 오염시키나

---

## 라운드 1 (redteam)

- **대상 commit**: main clean · **검토일**: 2026-07-02
- **판정**: **HOLD — blocker 1건** (major 3, minor 5)

### PASS (실물 대조)

- **PredictRequest 스키마** — `{patient_id: str, features: {col: float|None}}` = `app.py:77-79` 실물(`features: dict[str, float | None]`). 일치.
- **/predict 환자별 상태** — `app.py:92-97` `s["pred"].predict(req.patient_id, row)` — causal hidden-state 이어감. 근거 확인.
- **결측 계약(None→NaN, 0/평균 금지)** — `app.py:79`+`_row_from :88`(`... else np.nan`). CLAUDE.md 0-fill 금지와 정합.
- **워커 1 고정 근거** — `predictor.py:25 _h: dict[str,torch.Tensor]` 프로세스 인메모리, 도크스트링 "replicas=1 assumed". 워커≥2면 pid 상태 파편화. 확인.
- **reload 2배 창** — `_load_all app.py:58-68` build-new-then-swap, 옛/새 공존 창. 근거 확인.
- **replay_patient 존재** — `simulator.py:26-28` 실존(causal 순서). 단 **인프로세스 함수**라 HTTP Locust 직접 재사용 불가 — DDD "참고" 표기와 정합.
- **setB PSV 경로·로더** — `data/raw/training_setB/*.psv` 20,000개 실존. 재사용 로더 `replay/psv_source.py:PsvRowSource`가 PSV→`{col: float|None}` 시간순 dict 산출(`:41-48`) → /predict 스키마 정확 일치. N=1000<20000 환자 소진 없음.

### blocker (1건)

**B1 / 스트림 반복(재사용) 시 상태·메모리 오염, HTTP reset 부재 (결정 2)**

- **문제**: 결정 2가 "스트림 끝나면 반복(지속 부하)"을 규정하나 **반복 시 환자 상태 처리 미정**. 재사용 로더 도크스트링이 이 함정을 명문화 — `psv_source.py:10-12` **F4: "서버엔 리셋 엔드포인트가 없어, 같은 patient_id로 다시 틀면 서버가 이전 실행의 hidden state를 이어받아 곡선이 오염된다."** 실제 `app.py` 라우트는 `/predict·/health·/schema·/metrics·/admin/reload·/drift`뿐 — **`/reset` HTTP 엔드포인트 없음**(`predictor.py:36 reset()`은 있으나 미노출). 두 갈래 모두 측정 목표를 해침:
  - (A) 같은 pid 재사용 → hidden state 이월(causal 오염). latency엔 무해하나 상태가 "미래를 본" 채 재시작.
  - (B) F4 회피대로 매 반복 유니크 pid → `_h`·`_locks`(`predictor.py:25-26`)·StreamPreprocessor per-pid 버퍼·drift window(`app.py:105`) **무한 누적** → N=1000이 수천 RPS로 수분 지속 시 유니크 pid 수십만 → **부하 무관 단조 메모리 증가**가 결정 6(메모리 지표)·결정 7(mem_limit 2g OOM)을 **직접 오염**.
- **근거**: `psv_source.py:10-12`(F4), `app.py` 라우트(/reset 부재), `predictor.py:25-26`(pid별 무한 dict), 결정 2·6·7.
- **제안**: DDD에 반복/재사용 상태 전략 명시 — (i) 반복 안 하고 스트림 종료 시 User가 **다른 미사용 환자로 교체**(pid 유한, 상태·메모리 유계), 또는 (ii) 반복하되 메모리 단조 증가를 **측정 대상으로 명시**+지속시간·pid 카디널리티 상한 정의. 어느 쪽이든 결정 6/7 메모리 해석에 누적분 분리 규약. (reset 엔드포인트 추가는 프로덕션 코드 수정=범위 밖 → 교체 방식이 자연스러움.)

  **[reviser 응답]** 해소(v2). 옵션 (i) 채택. 결정 2에 "**스트림 종료 시 반복하지 않고 미사용 환자로 교체**" 확정 + "★반복 재사용 금지 근거 (B1)" 문단 신설 — `psv_source.py:10-12` F4·`app.py` 라우트에 `/reset` 부재(`predictor.py:36 reset()` 미노출) 실물 확인 인용, reset 추가는 범위 밖임을 명시. 결정 6에 "★메모리 해석 규약 (B1)" 추가: 교체로 pid 카디널리티 N개 유계 → 메모리 증가는 부하 신호지 누적 아티팩트 아님, N 고정인데 단조 증가하면 교체 불변식 위반(시나리오 버그)으로 취급. 결정 7도 "순간 메모리 2배"→"순간 메모리 증분"으로 완화 연동.

### major (3건)

- **M1 / 환자 배타 배정 불변식 미명시 (결정 2)** — "User 1=환자 1명"이라 하나 서로 다른 User가 distinct 환자를 잡는다는 불변식·충돌 회피 미명문. 두 User가 같은 pid 동시 전송 시 per-pid 락(`predictor.py:43`)이 직렬화는 하나 **두 스트림 timestep이 뒤섞여 causal 붕괴**. N≤1000 vs 20000 환자라 안전하나 "각 User는 배타적 distinct 환자 점유"를 요구사항으로 명시 필요.

  **[reviser 응답]** 해소(v2). 결정 2에 "★배타 배정 불변식 (M1)" 문단 신설 — "각 Locust User는 실행 내내 배타적 distinct 환자 점유"를 요구사항으로 명문화, 락 직렬화가 causal 붕괴를 못 막음·교체 시에도 미사용 pid에서만 뽑아 배타성 유지 명시. 20,000 > N≤1000 배정 가능도 [확인됨: `training_setB/*.psv` 20,000개]. (락 위치는 `predictor.py`의 `predict`가 `with self._lock(pid)` 획득으로 실물 확인 — 리뷰의 `:43`은 `_lock` 메서드/`:44` 획득 지점, 개념 일치.)

- **M2 / 정상상태·워밍업 컷 정성적 (결정 4.5)** — "초반 컷"에 몇 초·몇 요청인지 없음. 300-trial 캘리브레이션이 첫 /predict lazy-boot(`app.py:56-66`) → 프리웜 안 하면 첫 요청 수 초 → p99 오염. (i) 부하 전 서버 프리웜, (ii) 컷 창 정량(예: 첫 30s/첫 K요청), (iii) Locust 통계 리셋 명시 필요.

  **[reviser 응답]** 해소(v2). 결정 4.5를 "★워밍업 컷 정량 (M2)"으로 재작성 — (i) 부하 전 워밍 요청 1건으로 lazy-boot(`app.py` `state()`→`_load_all(force=False)`, 300-trial) 선태움, (ii) 램프업 후 **첫 30초 컷**, (iii) Locust `--reset-stats`(또는 정상상태 창 별도 집계). 수치는 [우리 결정], SM에서 정상상태 진입 지연 시 상향.

- **M3 / 무릎 판별 기준 정성적 (결정 5)** — "RPS 정체+p99 급증"에 몇 % 정체·급증인지 없음. 무릎은 헤드라인 산출물이므로 정량 임계(예: N 2배에 RPS 증가 <X%, p99 >Y ms) 필요. `[확인됨: Locust]`는 개념만 뒷받침, 수치는 우리 결정.

  **[reviser 응답]** 해소(v2). 결정 5에 "★무릎 정량 임계 (M3)" 추가 — **N 2배에 RPS 증가 <10%(정체) 또는 p99 > N=1 기준선의 3배(급증)**를 만족하는 첫 N을 무릎으로 판정(먼저 걸리는 쪽). 수치는 [우리 결정], Locust 문서는 정성 개념만 뒷받침으로 표기 정정.

### minor (5건)

- **m1 / N=1 "경합 0" 과장 (결정 5)** — Locust User 1 + serving 1워커가 같은 호스트 코어 공유. "최소 경합 기준선"으로 정정.

  **[reviser 응답]** 해소(v2). 결정 5 N축에 "N=1은 **최소 경합 기준선**(생성기·서빙 같은 호스트 코어 공유하므로 경합 0 아님)"으로 정정.

- **m2 / CPU 경고 오라클 무릎국면 정량논거 (결정 4.2/4.3)** — "<1 RPS 쟁탈 무시" 논거는 여유증명 지점만. 무릎 탐색은 수천 RPS라 실경합 → 경고가 무릎 전에 뜨면 "생성기의 무릎(무효)"임을 명시. 90% 미만 sub-threshold는 경고 사각(필요조건이지 충분조건 아님).

  **[reviser 응답]** 해소(v2). 결정 4.3(항목 3)에 "★무릎 탐색 국면 유의 (m2)" 추가 — 경고가 서빙 무릎보다 먼저 뜨면 그 무릎은 생성기의 무릎(무효)이므로 폐기·격리 후 재측정, 90% 미만 sub-threshold는 필요조건이지 충분조건 아님 명시.

- **m3 / reload latency 스파이크 vs p99 분리 (결정 7)** — reload 캘리브레이션이 in-flight /predict와 CPU 경합 → reload 창 p99 팽창. 단 핫패스는 `_LOCK` 미획득(`app.py:71-74`)이라 락 스톨 없음. SM-3 합격기준은 OOM/메모리지 latency 아님 → "SM-3 latency 창은 결정 5 p99에 합산 안 함" 명시.

  **[reviser 응답]** 해소(v2). 결정 7에 "★합격 기준 = OOM/메모리, latency 아님 (m3)" 추가 — reload 창 latency는 결정 5 p99에 합산 안 함(별도 창 기록). `_LOCK`은 `_load_all` 전용(`app.py:42`·`:55 with _LOCK`), `predict:92-107`은 미획득이라 락 스톨 없음 실물 확인 인용.

- **m4 / "reload RSS 2배" 보수적 (결정 7)** — torch 런타임(~248MB)은 1회 로드라 복제 안 됨; 스왑 창 복제는 bundle(가중치+reference+thr)뿐 → 실 peak ≪ 2×(2×248≈496MB<2048MB). "bundle 이중화 창"으로 선명화.

  **[reviser 응답]** 해소(v2). 결정 7 근거를 "★bundle 이중화 창 (m4)"으로 선명화 — torch 런타임 1회 로드라 복제 안 됨, 복제분은 bundle(가중치+reference+thr)뿐 → 실 peak ≪ 2×. `app.py:49-68 _load_all`의 build-new-then-swap(new_s/new_ds 빌드 후 리바인드) 실물 확인 인용. 결정 7 헤드라인 "RSS 2배"→"메모리 증분"으로 완화.

- **m5 / 재사용 로더 명명** — DDD "config.py 로더"라 하나 `config.py`엔 setB PSV 상수 없음(`DATA_DIR/training_setB` 글롭). 실 재사용점은 `replay/psv_source.PsvRowSource` → 핸드오프에서 명시 지목.

  **[reviser 응답]** 해소(v2). 결정 2 근거·결정 3에 재사용 로더를 `replay/psv_source.PsvRowSource`로 명시 지목(config.py엔 setB PSV 상수 없고 `DATA_DIR/training_setB` 글롭뿐임을 결정 3에 병기). 핸드오프도 이 지목 따름.

### 부하 실행 검증 항목 (SM / [검증 필요] — 설계 blocker 아님)

- **SM-3**: N=200 지속 중 `/admin/reload` → mem_limit 2g 내 OOM-kill 없음 (결정 7).
- **CPU 경고 무발생**: 무릎 지점/이하에서 Locust CPU 경고 침묵 여부 (결정 4.3).
- **무릎 위치**: 실측 무릎이 병원 부하의 몇 배 (결정 5).
- **정상상태 메모리 기울기**: B1 해소책(교체) 적용 후 지속 부하에서 메모리 유계인가 — B1 해결 검증용.

---

**blocker: 1건** (B1). blocker≠0 → **HOLD**. B1은 psv_source F4가 명문화한 함정 — 반복 재사용이 causal 오염(A) 또는 무한 메모리 누적(B)으로 결정 6/7 측정을 무효화. major 3·minor 5는 측정 유효성 보완 권고.

---

## 라운드 2 (redteam)

- **대상**: `decisions.md` v2 (레드팀 R1 반영) · **검토일**: 2026-07-02
- **핵심 질문**: B1 해소가 **서버측 pid 누적**을 실제로 막았나, 아니면 클라이언트(User=N) 유계만 보고 서버 누적을 놓쳤나
- **판정**: **HOLD — blocker 1건** (major 1, minor 2)

### PASS (v2 보완 + 실물 재대조)

- **M1 배타 배정 불변식** — `decisions.md:40` "각 User는 배타적 distinct 환자 점유" 명문화. 락 직렬화가 causal 붕괴 못 막음 근거 `predictor.py:29-43` 정합.
- **M2 워밍업 컷 정량** — 프리웜 1건·첫 30s 컷·`--reset-stats` 확정, lazy-boot `app.py:71-73` 확인.
- **M3 무릎 정량 임계** — "N 2배에 RPS <10% OR p99 > N=1 3배" 재현 기준. 수치 `[우리 결정]` 표기(단 m-r2-2 참조).
- **m3 `_LOCK` 핫패스 미접촉** — `app.py:55 with _LOCK`은 `_load_all` 전용, `predict :92-107` 미획득. reload 창 latency 분리 근거 확정.
- **m4 bundle 이중화 창** — `app.py:62-68` build-new-then-swap, torch 1회 로드. 정합.
- **m5 로더 명명** — `PsvRowSource` 지목, 출력 `{col:float|None}`(`psv_source.py:41-48`)이 스키마 일치.
- **reset 부재** — `predictor.py:36 reset()` 존재하나 라우트 미노출. B1 회피 전제(프로덕션 미수정) 확인.

### blocker (1건)

**B1-r2 / "교체로 pid 유계"가 서버 메모리엔 안 통한다 — 결정 6 해석 규약이 실물과 어긋남 (결정 2·6·7)**

- **문제**: reviser는 B1을 "미사용 환자 교체 → pid 카디널리티 N개 유계 → 무한 누적 차단"(`decisions.md:41`)으로 해소 주장하나, 이는 **클라이언트(동시 User=N)만 유계**. **서버는 옛 pid 엔트리를 절대 GC 안 함** — 교체가 서버 dict를 못 비운다:
  - `_h` — `predictor.py:49 self._h[pid]=h_n`, 삭제 없음(미노출 `reset() :38`뿐).
  - `_locks` — `predictor.py:33 self._locks[pid]=Lock()`, 삭제 없음.
  - `_last`(ffill 버퍼) — `preprocess_rt.py:43 self._last[pid]=state`, 삭제 없음(미노출 `reset() :27`).
  - 서버가 본 **distinct pid 총수는 동시 N이 아니라 누적** 증가. 결정 6의 "메모리 증가는 부하(동시 N) 신호이지 pid 누적 아티팩트 아님"(`:79`)과 진단 규칙 "N 고정인데 단조 증가 = 시나리오 버그"는 **인과가 뒤집힘** — 올바른 교체를 해도 서버 메모리는 누적 pid로 정상적으로 완만히 상승하는데 이를 "버그"로 오진하게 지시.
- **누적 상한·지속시간 정책이 결정 6/7 결론을 가르는데 둘 다 미정**:
  - (A) 엄격히 "미사용 only"면 누적 ≤ setB 20,000. 크기 ≈ 수십 MB → 결정 7 OOM은 **우연히 안전**(N 유계 기전이 아니라 유한 풀 상한 덕). 단 20,000 소진 후 교체 대상 고갈 → **지속 부하(SM-3 N=200 지속·장시간 무릎 탐색)에서 동작 미정의**(결정 2에 고갈 처리 없음).
  - (B) 고갈 견디려 `run_suffix`(`psv_source.py:38`) 재활용하면 서버 pid **진짜 무한 증가** → 결정 6 정확히 틀리고 결정 7 OOM 되살아남.
  - 어느 horn이든 B1 해소 전제("교체로 무한 누적 차단")·결정 6 규약 불성립 = **가짜 수렴(서버 누적 놓침)**.
- **근거**: `predictor.py:25-26·33·38·49`, `preprocess_rt.py:24·27·43`(서버 pid 무 GC), `decisions.md:41·79·83`, setB 20,000.
- **제안(설계부 수준)**: 결정 6 해석 규약을 서버 실물에 맞게 정정 — (i) "동시 N은 유계지만 **서버 누적 distinct pid는 유한 풀(20,000)로 상한**, 완만한 단조 상승은 정상"으로 진단 규칙 반전, (ii) **지속시간/풀 고갈 정책을 결정으로 명시**(풀 소진 전 종료 or 칸마다 서버 재시작 or `run_suffix` 채택 시 단조 상승을 "예상 아티팩트"로 결정 6에서 분리), (iii) 결정 7 OOM 판정을 "누적분(풀 상한 수십 MB) 제외 후 reload 순간 증분"으로 규정.

  **[reviser 응답]** 해소(v3). 실물 재확인: `predictor.py:49 self._h[pid]=h_n`·`:33 self._locks[pid]=Lock()`·`preprocess_rt.py:43 self._last[pid]=state` — 세 dict 모두 predict/step 경로에 **삭제 코드 없음**, `reset()`은 `predictor.py:36`·`preprocess_rt.py:27`에 존재하나 HTTP 라우트 미노출([확인됨]). 서버 distinct pid는 동시 N이 아니라 누적 증가함을 확정. **세 정정 반영**: (i) 결정 6 "★메모리 해석 규약"을 **B1-r2로 인과 반전** — R1이 클라이언트 유계만 본 것을 명시하고, 서버 누적은 미사용 교체 시 유한 풀 20,000으로 상한(≈ 수십 MB, pid당 hidden ~1KB + ffill 수백 B 산수 [유도]) → 완만한 단조 상승은 정상, 진단 규칙을 "풀 상한 초과 급격/무한 상승이라야 이상"으로 반전. (ii) 결정 2에 "★지속시간·풀 고갈 정책 (B1-r2)" 추가 — 칸마다 풀 고갈 전 유한 지속·소진 임박 시 서버 재시작·`run_suffix` 무한 pid 재활용 불채택(택하면 결정 7 OOM 부활) 명시. (iii) 결정 7에 "★OOM 판정 = 누적분 분리 후 reload 순간 증분 (B1-r2)" 추가 — 누적분(수십 MB baseline)을 빼고 reload 증분만으로 OOM 판정. R1이 "교체로 무한 누적 차단"으로 클라이언트만 유계로 본 것을 서버 누적까지 정정함.

### major (1건)

**M-r2-1 / N-스윕 다중 Locust 런 ↔ 단일 서버 프로세스 pid 스코프 미정 (결정 2·5)**

- **문제**: 결정 5 N축 6칸을 코어 고정 시 컨테이너 재시작 없이 돈다(재시작은 "코어 전환마다"만, `:69`). 칸마다 미사용 풀을 앞에서 재할당하면 **이전 칸 pid 재사용** → 서버가 옛 hidden state 이어받아 B1-(A) causal 오염 재발(단 헤드라인이 latency·RPS·메모리라 예측값 오염 실질 영향은 제한적). 전역 풀이면 6칸 누적으로 20,000 고갈 앞당김. 칸 간 used-set 스코프·서버 재시작 정책 미명시.
- **제안**: "N칸 간 서버 상태 스코프" 결정 추가 — N칸마다 서버 재시작(깨끗) or 전역 미사용 풀(고갈 관리 연동). B1-r2 고갈 정책과 함께.

  **[reviser 응답]** 해소(v3). 결정 5에 "★N칸 간 서버 상태 스코프 (M-r2-1)" 추가 — N축 6칸이 코어 고정 시 재시작 없이 이어 돌면 이전 칸 서버 pid 상태(`_h`·`_locks`·`_last`)가 이월돼 pid 재사용 causal 오염(B1-(A)) 또는 전역 누적 풀 고갈 앞당김이 생김을 명시하고, **권장: N칸마다(최소한 N 크게 바꿀 때) 서버 재시작**으로 깨끗한 상태에서 측정. 코어 전환 재시작·B1-r2 풀 고갈 정책과 하나의 재시작 규율로 정합.

### minor (2건)

- **m-r2-1 / drift window "무한 누적" 표현 부정확** — drift window는 `window.py:26 deque(maxlen=5000)` **하드 캡**이라 pid 무관 상한 있고, 적재도 aux 게이트(`app.py:104`) 뒤. R1·v2 인용 "drift window 무한 누적"은 실물과 다름 — 무한 누적 주체는 `_h`·`_locks`·`_last`뿐. 표현 정정(B1-r2에 흡수 가능).

  **[reviser 응답]** 해소(v3). 실물 확인: `window.py:26 deque(maxlen=5000)` 하드 캡·`app.py:104 if metrics._aux_metrics_enabled(): get_window().add(...)` aux 게이트([확인됨]). 결정 2 "★반복 재사용 금지 근거"와 결정 6 "★메모리 해석 규약"에서 drift window를 **무한 누적 주체에서 제외**하고 상한 있음을 명기, 무한 누적 가능 주체를 `_h`·`_locks`·`_last`뿐으로 바로잡음(그마저 미사용 교체 하 풀 상한으로 유계).
- **m-r2-2 / N=1 p99 기준선 표본수** — "p99 > N=1 3배" 기준선이 저 RPS라 컷 창 표본 적어 p99 추정 흔들림. 최소 표본수 충족(또는 기준선 p95/충분표본 구간) 권고. 임계 개념은 유효.

  **[reviser 응답]** 해소(v3). 결정 5 "★무릎 정량 임계"에 "★기준선 표본수 (m-r2-2)" 한 줄 추가 — N=1 기준선 p99는 **정상상태 창에서 최소 표본수 충족 구간에서 산정**(표본 부족 시 창 연장 또는 p95 대체). 임계 개념(3배)은 유지.

### 부하 실행 검증 항목 (SM 추가)

- 정상상태 메모리 기울기: 교체 후 서버 누적(풀 상한 수십 MB)이 유계인지 — B1-r2 정정 검증용.
- 풀 고갈: 장시간 N=1000/SM-3 지속에서 20,000 풀 고갈 도달 여부.

---

**blocker: 1건** (B1-r2). blocker≠0 → **HOLD**. B1 R1 해소는 클라이언트 동시 점유(N)만 유계로 만들었을 뿐 **서버 dict(`_h`·`_locks`·`_last`)는 옛 pid GC 안 해 누적**(`predictor.py`·`preprocess_rt.py` 실물). 결정 6 해석 규약 인과가 뒤집혔고 풀 고갈/지속 정책 미정이라 누적 상한(20k vs 무한)이 결정 6/7 결론을 가르는데 미결 = 가짜 수렴. 정정은 설계부 수준(해석 규약 반전 + 고갈/지속 정책 결정)에서 가능.

---

## 라운드 3 (redteam) — 최종 수렴 판정

- **대상**: `decisions.md` v3 (레드팀 R2 반영) · **검토일**: 2026-07-02
- **핵심 질문**: R2 B1-r2 정정(서버 누적 인과 반전 + 고갈/지속 정책)이 실물과 정합하고 새 모순을 안 만드나
- **판정**: **PASS — blocker 0건** (major 0, minor 3)

### PASS (R2 보완 실물 재대조, 가짜 수렴 아님)

- **B1-r2 서버 무 GC 실물 확정** — `predictor.py:49 _h[pid]=`·`:33 _locks[pid]=`·`preprocess_rt.py:43 _last[pid]=` 모두 predict/step 경로에 삭제 없음. 삭제는 `predictor.py:38`·`preprocess_rt.py:27`(reset 내부)뿐이고 `_locks`는 reset조차 안 비움. `reset()`은 존재하나 라우트 미노출(`/predict·/health·/schema·/metrics·/admin/reload·/drift`만). **인과 반전 정정("서버 누적 pid는 유한 풀 20,000 상한, 완만 단조 상승 정상")이 실물과 정확히 정합**. R1이 클라이언트만 유계로 본 것을 정직히 바로잡음.
- **B1-r2 지속·고갈 정책** — (1) 유한 지속·풀 고갈 전 종료, (2) 소진 임박 시 칸/코어 전환마다 서버 재시작, (3) `run_suffix` 무한 pid 불채택 — 세 갈래 결정으로 명문화.
- **결정 7 OOM 판정** — "누적분(풀 상한 수십 MB) baseline 분리 후 reload 순간 증분(bundle 이중화 창)으로만 판정". `_load_all` build-new-then-swap(`app.py:62-68`) 정합, reload 창 latency 미합산(`_LOCK` `_load_all` 전용, predict 미획득) 확인.
- **M-r2-1 N칸 재시작** — "N칸마다 서버 재시작"이 코어 전환 재시작·고갈 정책과 하나의 재시작 규율로 정합. "코어 고정 시 재시작 없음"을 명시적 오버라이드(충돌 아님).
- **m-r2-1 drift window** — `window.py:26 maxlen=5000` 하드 캡·aux 게이트(`app.py:104`) 뒤 확인. 무한 누적 주체에서 제외 정합.
- **m-r2-2 기준선 표본수** — "N=1 p99는 정상상태 창 최소 표본수 충족 구간 산정" 추가 확인.
- **재사용 로더·스키마** — `PsvRowSource` PSV→`{col:float|None}`(`psv_source.py:41-48`)이 `PredictRequest`(`app.py:77-79`) 정확 일치.

### blocker

- **없음.** R2 보완 모두 실물 정합, 인과 반전이 새 모순 안 만듦. 신규 [확인됨] 4건 줄 단위 일치. **가짜 수렴 아님 — 서버 누적을 실물로 짚어 정정 완료.**

### minor (3건)

- **m-r3-1 / 재시작마다 프리웜 재실행 미명시** — M-r2-1이 N칸마다 서버 재시작을 요구하는데 결정 4.5(i) 프리웜은 "부하 전 1건"으로만 서술 → "매 재시작마다 재실행"이 명시적이지 않음. **다만 4.5(ii) per-cell 첫 30초 컷이 lazy-boot(수 초)를 흡수 → 측정 무효화 아님(그래서 minor)**. 제안: 4.5(i)에 "재시작 칸마다 프리웜 재실행" 추가(백스톱은 per-cell 컷). **→ 핸드오프 러너에 반영 예정.**
- **m-r3-2 / "2배" 표현 잔재** — 결정 7 제목·범위표의 "reload 2배 OOM"에 옛 표현 잔재. 본문(`:95 순간 증분`·`:98 bundle 이중화 창, torch 1회 로드`)이 정정하나 제목/표는 SM-3 상속명. 제안: 제목/표도 "reload 순간 증분 OOM"으로 통일.
- **m-r3-3 / 줄 참조 off-by-one** — `window.py:26`은 시그니처(`=5000` 리터럴 있음), deque 생성은 :27 등. 클레임 자체는 TRUE, 참조 정밀도 문제. 사실 오류 아님.

---

**blocker: 0건 — PASS (최종 수렴).** R1(B1)·R2(B1-r2) 두 blocker가 각각 실물 근거로 해소, v3 정정이 서버 누적 인과를 실제 코드(무 GC)와 정합시키며 새 모순 없음. 남은 것은 minor 3건뿐 — 억지 blocker 없이 정직히 PASS. 핸드오프·구현 진행 가능. (minor m-r3-1·m-r3-2는 핸드오프/구현에 반영.)
