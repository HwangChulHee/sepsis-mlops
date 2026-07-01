# 리플레이어 핸드오프 — 라운드 (다): 위험도 곡선 가시화 + 다중 동시 스트림

> 선행: (가)(e8ef105) 엔진·.psv 어댑터·CLI, (나)(99b7c67) 실측 E2E 증거.
> 라운드=커밋. **푸시는 사람 게이트(자동 금지)** — (가)(나) 관례 유지.

---

## 0. 한 줄 요약

녹화 환자를 흘려보낼 때 **한 화면에서 환자별 위험도 선**이 시간축으로 오르내리는 걸 보고,
**여러 환자를 동시에**(병동) 흘릴 수 있게 한다. (가)의 무상태 엔진(F5) 위에 N개 source를
스레드로 얹고, 서빙에 환자별 최신 위험도 Gauge를 (옵트인으로) 추가한다.

## 1. 범위 (이번 라운드 = 다)

- **서빙 메트릭**: `serve_pred_prob_latest{patient_id}` Gauge 추가 — 환자별 *최신* p.
  현재 `serve_pred_prob`는 Histogram(분포)이라 "한 환자의 위험도 선"이 안 그려진다 `[확인됨: metrics.py]`.
  Gauge라야 Grafana가 patient_id별 시계열 라인을 그린다.
- **다중 동시 스트림**: `replay_many(sources, sender, *, speed, sleep_fn, max_workers)` —
  무상태 엔진을 source당 스레드 1개로 동시 구동. 환자 분리는 **서버 hidden state(patient_id) +
  source별 독립 커서**로 보장(엔진은 여전히 환자 0보유, F5).
- **Grafana 패널**: drift 대시보드에 "Per-patient risk (latest p)" timeseries 추가.
- **병동 CLI**: `scripts/replay_ward.py` — glob/목록의 .psv N개를 동시 재생(데모 가시성).

**제외 (범위 밖 — 정직하게 분리):**
- 알람 라벨 정밀 정렬(임상 onset vs τ 교차 시점). → utility score 몫(h2). (나) §4와 동일 입장.
- async/이벤트루프 재작성. → 스레드로 충분(sender·sleep이 블로킹 I/O). 필요해지면 별건.
- 분산/멀티 replica 서빙에서의 Gauge 수집. → console-prep "단일 프로세스 전제"와 동일 빚.

## 2. 카디널리티 결정 (이 라운드의 진짜 설계 포인트) `[우리 결정]`

환자별 라벨 Gauge는 **무한 카디널리티**다 — `/predict`는 실트래픽 핫패스이고 PhysioNet엔
40,336명이라, patient_id를 라벨에 박으면 영원히 안 사라지는 시계열이 환자 수만큼 쌓인다
(전형적 Prometheus 지뢰).

- **결정**: `serve_pred_prob_latest`는 **기본 OFF, `SERVE_PER_PATIENT_GAUGE=1`일 때만 기록**한다.
  리플레이/데모만 켜고, 프로덕션 서빙은 끈 채 둔다. 이는 프로젝트의
  **"결측 마스크 기본 OFF(옵트인)"** 철학과 같은 패턴 — 누수/footgun은 명시적 옵트인 뒤에 둔다.
- 플래그는 **호출 시점에 동적으로 읽는다**(모듈 import 시 상수화 금지) — 테스트가 토글 가능해야 하고,
  운영 중 토글도 자연스럽다.
- OFF일 때 Gauge 메트릭 객체는 등록되되 라벨 시계열은 **0개**(`.labels().set()`을 안 부르므로).
- remove(자동 만료)는 이번 범위 밖 — 옵트인만으로 footgun을 닫는다(데모는 환자 수가 작아 경계됨).

## 3. 시그니처 (신규/변경)

### 3.1 `sepsis.serve.metrics`
```python
PRED_PROB_LATEST = Gauge("serve_pred_prob_latest",
    "latest predicted risk per patient (OPT-IN — unbounded label; see round c)",
    ["patient_id"])

def record(latency_s, p, alarm, raw_row, feature_names, patient_id=None) -> None:
    # 기존 동작 동일 + (patient_id 주어지고 SERVE_PER_PATIENT_GAUGE 켜졌을 때만)
    #   PRED_PROB_LATEST.labels(patient_id=patient_id).set(p)
```
`patient_id`는 **뒤에 추가된 키워드 기본값 None** — 기존 호출부 비파괴(B-호환).

### 3.2 `sepsis.replay.orchestrator`
```python
def replay_many(sources, sender, *, speed,
                sleep_fn=time.sleep, max_workers=None) -> list[list[dict]]:
    # source당 스레드 1개로 replay_stream 동시 구동.
    # 반환: 입력 sources 순서와 같은 인덱스의 응답 리스트들의 리스트.
```
- **sender는 스레드 안전이어야 함**(공유). `HttpSender`(httpx.Client)는 스레드 안전 `[확인됨: httpx 문서]`.
- 빈 sources → `[]`.

## 4. 실패 모드 (테스트가 거울로 막을 것)

- **F-c1 환자 섞임**: 같은 patient_id 둘 이상을 동시에 틀면 서버 hidden state가 충돌해 곡선 오염.
  → `replay_many`는 **중복 patient_id 발견 시 ValueError**(F5 거울; run_suffix로 유일화 유도).
- **F-c2 순서 깨짐**: 동시 실행에도 *각 환자 내부* 행 순서는 0→1→…→T-1 보존(엔진 F2가 보장,
  스레드는 환자 간 인터리브만 허용). 테스트는 환자별로 묶어 순서 단언(환자 간 순서는 단언 안 함).
- **F-c3 카디널리티 누수**: OFF 기본인데 켜진 채 새어나감. → 플래그 OFF면 `serve_pred_prob_latest`
  시계열 0개임을 단언. ON이면 해당 patient_id에 마지막 p가 박힘을 단언.
- **F-c4 B-호환 깨짐**: `record`에 patient_id 추가가 기존 5-인자 호출을 깨면 안 됨 → 기본값 None.

## 5. 합격 기준 (테스트 매핑)

1. **Gauge 옵트인** — `SERVE_PER_PATIENT_GAUGE` OFF면 시계열 0개, ON이면 `{patient_id}`에 최신 p.
   여러 번 record하면 **마지막** p로 갱신(Gauge=최신값).  → `tests/console_prep/test_pred_prob_latest_gauge.py`
2. **record B-호환** — patient_id 없이 5-인자 호출이 그대로 동작(기존 호출부 비파괴).  → 〃
3. **동시 분리** — N개 source 동시 재생 시 각 환자가 자기 행만 순서대로 받고, 결과는 입력 순서로 인덱싱.  → `tests/replay/test_orchestrator.py`
4. **중복 가드** — 중복 patient_id면 ValueError(F-c1).  → 〃
5. **빈 입력** — sources 빈 리스트 → `[]`.  → 〃
6. **정직성** — 엔진은 여전히 무상태(orchestrator는 source/sender만 배선, 환자 저장소 0).  → 〃 (구조 단언)

## 6. 데모 배선 (CLI)

`scripts/replay_ward.py --glob "data/.../p*.psv" --speed 7200 --base-url ... [--limit N]`
→ 각 .psv를 유일 run_suffix로 PsvRowSource화 → 공유 HttpSender → `replay_many`.
서버는 `SERVE_PER_PATIENT_GAUGE=1`로 띄워야 Grafana 라인이 보인다(README에 명시).

## 7. 다음 라운드 예고 (이번엔 손대지 말 것)

- Gauge **자동 만료(remove)** — 스트림 종료 시 시계열 정리(켠 채 장기 운영할 때).
- 멀티 replica 서빙에서의 per-patient Gauge 수집(공유 신호/푸시게이트웨이) — 단일 프로세스 빚 상환.
