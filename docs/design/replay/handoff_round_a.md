# 리플레이어 핸드오프 — 라운드 (가): 스트림 엔진 (HTTP는 테스트에서 가짜)

> 대상 구현자: Claude Code · 설계 결정: 이 문서가 권위(별도 decisions.md 없음, 복잡도 낮아 레드팀 스킵)
> 패턴: spec-writer가 **이 문서만** 보고 RED 작성(src/ 안 봄) → 구현 → 픽스처 격리.
> 라운드=커밋. 푸시는 사람 게이트(자동 금지).

---

## 0. 한 줄 목적

녹화된 환자 데이터를 **재생 버튼 누르듯** 시간 간격대로 서빙 `/predict`에 흘려보내는 장치. 이번 라운드는 그 **엔진 + `.psv` 어댑터**만 닫는다. 진짜 서버 호출(E2E)·다중 동시 스트림·위험도 곡선 검증은 **다음 라운드**.

---

## 1. 범위 (이번 라운드 = 가)

**포함:**
- 스트림 엔진: 소스에서 행을 시간순으로 꺼내 → `features` dict로 → 같은 `patient_id`로 sender에 넘김 → 다음 행까지 잔다.
- `.psv` 어댑터: PhysioNet 환자 파일을 읽어 featureset 컬럼만 잘라 행을 내준다(NaN→null).
- httpx sender: `{base_url}/predict`에 POST.
- CLI 스크립트: 진짜 어댑터+sender+`time.sleep`을 배선해 환자 한 명 튼다.

**제외 (다음 라운드/별건 — [범위 외]로 정직하게 분리):**
- 진짜 서버에 실제로 꽂는 E2E(minikube 합성번들). → **다음 라운드.** 콘솔 E2E처럼 엔진 버그와 배포/네트워크 버그를 안 섞으려고 분리.
- 다중 환자 **동시** 오케스트레이션(스레드/async로 N개 동시). → 다음 라운드. 단, 이번 엔진은 **무상태로 짜서** N개로 자연 확장 가능해야 함(§4 실패모드 F5).
- 한 환자 위험도 **선** 그래프용 Gauge 메트릭 + Grafana 패널. 현재 `serve_pred_prob`는 Histogram(분포)이라 한 환자 곡선은 안 그려짐 — 이건 별도 결정으로 미룸. [검증 필요]
- 진짜 위험도 곡선(`p` 0.1→0.7 오르내림) 검증. 학습된 모델 필요(사용자 다른 PC). 이 라운드는 "행이 제 박자·제 순서로, 안 섞이고, null 처리 맞게 꽂힌다"까지만.
- ICULOS 델타 기반 가변 간격. 이 데이터셋은 시간당 균일(1행=1시간)이라 고정 간격으로 충분. [확인됨: PhysioNet 2019 hourly]
- `/schema` 자동 협상. 어댑터는 featureset를 인자로 받아 `C.featureset_columns(fs)`를 그대로 신뢰.

---

## 2. 서빙 계약 (이미 존재 — 엔진이 맞춰야 할 사실) [확인됨]

`src/sepsis/serve/app.py` 읽어 확인:

- `POST /predict`, body = `{"patient_id": str, "features": dict[str, float|None]}`.
- 응답 = `{"patient_id", "p", "alarm", "featureset"}`.
- **결측 계약**: 안 잰 feature는 `null`(JSON) / 키 생략. 서버가 `None→np.nan`으로 받아 ffill·정규화까지 다 한다(`preprocess_rt.py`). **리플레이어는 절대 0/평균으로 안 채운다 — raw 값 + null만 보냄.** (0-fill = train-serving skew.)
- **hidden state는 서버가 `patient_id`별로 보유**(`StatefulPredictor._h`). 그러니 엔진은 같은 환자면 같은 id로만 쏘면 상태가 이어짐. 엔진은 hidden state를 몰라도 됨.
- **알 수 없는 feature 키 → 422.** 어댑터는 `C.featureset_columns(fs)`의 키만(부분집합 허용되나, 누락은 null로 채워 풀셋 전송 권장).
- **리셋 엔드포인트 없음.** `predictor.reset()`은 있으나 HTTP로 노출 안 됨 → §4 F4(재실행 시 stale state) 주의.

전처리는 **전부 서버 몫**. 엔진은 raw featureset 값을 그대로 옮기기만 한다(ffill/clip/z-score 금지).

---

## 3. 모듈 구조 (신규, 충돌 없음 — `src/sepsis/replay/` 미존재 확인)

```
src/sepsis/replay/__init__.py
src/sepsis/replay/engine.py        # 핵심 엔진 + RowSource/Sender 프로토콜
src/sepsis/replay/psv_source.py    # PhysioNet .psv 어댑터
src/sepsis/replay/http_sender.py   # httpx POST /predict (httpx>=0.28 이미 의존성에 있음)
scripts/replay/replay_patient.py          # CLI 배선
tests/replay/test_engine.py        # 가짜 source+sender+sleep
tests/replay/test_psv_source.py    # 임시 .psv → 행
```

### 3.1 프로토콜·시그니처 (계약 명문화)

```python
# engine.py
from typing import Protocol, Iterator

class RowSource(Protocol):
    patient_id: str
    def __iter__(self) -> Iterator[dict[str, float | None]]: ...
    # 각 yield = 한 타임스텝. 키 = featureset 컬럼. 결측 = None. (어댑터가 NaN→None 변환)

class Sender(Protocol):
    def send(self, patient_id: str, features: dict[str, float | None]) -> dict: ...
    # /predict 응답(또는 그에 준하는 dict) 반환. 엔진은 이 라운드에서 응답을 수집만 함.

def replay_stream(
    source: RowSource,
    sender: Sender,
    *,
    speed: float,
    sleep_fn=time.sleep,          # 주입 가능 — 테스트는 가짜로 sleep 인자를 기록
) -> list[dict]:
    """source의 행을 시간순으로 sender에 흘린다. 행 사이 간격 = 3600/speed 초.
    causal: i번째엔 행 i만 보냄(순서 보존, 건너뜀/중복/역순 없음).
    엔진은 patient_id를 source에서만 받아 매 send에 그대로 넘김(엔진 내부 환자 상태 0).
    반환 = send 응답들의 리스트(검사용).
    """
```

**sleep 의미 (정확히):**
- 행 0은 즉시 전송(앞에 sleep 없음).
- 행 1..T-1은 각각 전송 **전에** `sleep_fn(3600.0 / speed)`.
- 결과: T개 전송, **T-1번** sleep. 마지막 뒤 trailing sleep 없음.
- `speed=3600` → 1.0초, `speed=7200` → 0.5초, `speed=1` → 3600초(실시간).
- `speed <= 0` → `ValueError`.

### 3.2 `.psv` 어댑터

```python
# psv_source.py
class PsvRowSource:
    def __init__(self, path, featureset: str = "vitals",
                 patient_id: str | None = None, run_suffix: str | None = None):
        ...
    # - pandas read_csv(path, sep="|") (PhysioNet pipe-separated, 헤더 있음)
    # - C.featureset_columns(featureset) 컬럼만 선택 (SepsisLabel 등 비-feature 제외)
    # - NaN → None (float|None). 0/평균 채움 금지.
    # - 파일 순서 그대로 행 yield (= 시간순; 정렬·재배치 금지)
    # - patient_id: 명시값 우선. 없으면 파일 stem(예: "p000023").
    #   run_suffix 주어지면 "{stem}-{run_suffix}"로 붙임 (§4 F4: stale state 회피).
```

### 3.3 CLI

```
scripts/replay/replay_patient.py
  --psv PATH            (필수) 환자 .psv 경로
  --base-url URL        (기본 http://localhost:8000)
  --speed FLOAT         (기본 3600.0 = 1시간을 1초로)
  --featureset NAME     (기본 vitals)
  --run-suffix STR      (기본: 짧은 timestamp/uuid — 매 실행 fresh patient_id 보장)
```
배선: `PsvRowSource(...) → HttpSender(base_url) → replay_stream(..., speed=..., sleep_fn=time.sleep)`.
표준출력에 행별 응답(p, alarm) 한 줄씩 찍어 데모 가시성 확보.

---

## 4. 실패 모드 (구현이 반드시 피해야 할 것 — 합격 기준의 근거)

- **F1 — 0-fill skew.** 결측을 0/평균으로 채우면 train-serving skew. **반드시 null.** 서버가 ffill/평균 처리함. 엔진·어댑터는 전처리 일절 금지.
- **F2 — causal 깨짐.** 행을 정렬/재배치/건너뜀/중복하면 시간 인과가 깨져 hidden state가 오염. 파일/소스 순서 그대로, 1→2→…→T.
- **F3 — 알 수 없는 키 422.** featureset 밖 컬럼(SepsisLabel, ICULOS 등)을 features에 넣으면 서버 422. `C.featureset_columns(fs)`로 엄격히 한정.
- **F4 — 재실행 stale state.** 리셋 엔드포인트가 없어, 같은 `patient_id`로 또 틀면 서버가 이전 실행의 hidden state를 이어받아 곡선이 오염됨. → 어댑터/CLI가 **run마다 유일한 patient_id**(run_suffix)를 만들어 회피. 이 한계와 회피책을 코드 주석에 명시.
- **F5 — 엔진이 환자 상태를 들고 있음.** 엔진이 patient_id를 캐시/전역에 박으면 다중 스트림에서 환자가 섞임. **엔진은 무상태**여야 함 — patient_id는 매 호출 source에서 흘러나와 send로 그대로 전달, 엔진 내부엔 환자별 저장소 0. (다음 라운드 다중 확장의 전제.)
- **F6 — speed≤0 / 음수 sleep.** 가드 없으면 무한/음수 대기. `speed<=0`은 `ValueError`.

---

## 5. 합격 기준 (spec-writer RED 타겟 — 설계만 보고 작성, src/ 안 봄)

가짜 source(손으로 만든 dict 행) + 가짜 sender(호출 받아적는 우체통) + 가짜 sleep(인자 기록)으로 **모델·서버 없이** 전부 검증된다.

1. **호출 수·순서 (F2)**: T행 source → 정확히 T번 send. 전달된 행이 0,1,…,T-1 순서 그대로(건너뜀·중복·역순 없음).
2. **결측 = null (F1)**: source가 어떤 feature를 None으로 주면, 그 send의 features에서 해당 키가 `None`(0/평균 아님). 풀셋 전송 시 누락 키는 None으로.
3. **키 한정 (F3)**: send에 넘어간 features 키 집합 ⊆ `C.featureset_columns(fs)`. featureset 밖 키 없음.
4. **sleep 간격·speed 스케일 (F6)**: 기록된 sleep 인자들이 모두 `3600/speed`. `speed=3600→1.0`, `7200→0.5`, `1→3600`. sleep 호출 수 == T-1(첫 행 앞 없음, 마지막 뒤 없음). `speed<=0` → `ValueError`.
5. **patient_id 무상태 플러밍 (F5)**: 모든 send에 source.patient_id가 그대로 실림. 서로 다른 두 source를 같은 가짜 sender로 각각 돌리면, 각 호출의 patient_id가 자기 source 것(뒤섞임 없음). 엔진에 환자별 전역/캐시 상태 없음(구조로 확인).
6. **.psv 어댑터**: 임시 `.psv`(파이프 구분, 헤더, 일부 셀 `NaN`) → featureset 컬럼만, NaN→None, 파일 순서대로 행 yield. patient_id = stem(+run_suffix). 비-feature 컬럼(SepsisLabel) 미포함.
7. **(정직성) 전처리 부재**: 어댑터/엔진 출력 어디에도 ffill·clip·정규화 흔적 없음 — raw 값 또는 None만. (서버가 다 한다는 계약 §2 보존.)

---

## 6. 합리적 가정 (구현 자율 — 막히면 이 기본값)

- HTTP 클라이언트 = `httpx`(동기). sender는 `httpx.Client` 주입 가능하게(테스트는 fake transport 또는 fake sender).
- `.psv` 파싱 = `pandas.read_csv(sep="|")`. (cache.py와 동일 방식.)
- 행 dict 값 타입 = `float | None`(np.float를 파이썬 float로 캐스팅해 JSON 직렬화 안전).
- 응답 비수집이 기본이나, 엔진이 리스트로 모아 반환(디버깅·데모용). 메모리 우려 없음(환자당 수십 행).
- 로깅은 표준출력 한 줄/행(CLI). 라이브러리 코드는 print 금지.

---

## 7. 다음 라운드 예고 (이번엔 손대지 말 것)

- **(나) 실측 E2E**: minikube 서빙 + 합성번들로 진짜 `/predict` 때려 응답·메트릭 확인.
- **다중 동시 스트림**: §4 F5 무상태 엔진 위에 N개 source를 동시 구동(스레드/async). 환자별 분리는 서버 hidden state + 독립 source 커서로 보장.
- **위험도 곡선 가시화**: Gauge 메트릭 + Grafana 시계열 패널(현재 Histogram만 있음). 별도 결정 필요.
