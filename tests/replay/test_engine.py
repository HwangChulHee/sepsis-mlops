"""리플레이어 라운드 (가) — 스트림 엔진 TDD RED.

권위 출처(이것만 신뢰): design/replay/handoff_round_a.md.
**src/sepsis/replay/ 구현 코드는 일절 읽지 않았다** — 핸드오프 §3(시그니처)·
§4(실패모드)·§5(합격기준)가 못 박은 계약을 그대로 신뢰해 작성한다.
구현(src/sepsis/replay/)이 아직 없으니 지금은 RED(ModuleNotFoundError)가 정상이다.

신뢰 근거(핸드오프가 명문화한 신규 심볼·시그니처):
- sepsis.replay.engine.replay_stream(source, sender, *, speed, sleep_fn=time.sleep)
    -> list[dict]                                                   (handoff §3.1:75-87)
- sleep 의미: T개 전송 / T-1번 sleep / 행0 즉시 / 간격=3600/speed / speed<=0 -> ValueError
                                                                    (handoff §3.1:89-94)
허용된 기존 공개 의존성(SUT 아님): sepsis.config.featureset_columns (handoff §2·§3).

이 파일이 커버하는 합격기준(handoff §5):
  1 호출 수·순서  2 결측=null  3 키 한정  4 sleep 간격·speed 스케일
  5 patient_id 무상태 플러밍  7 정직성(전처리 부재)
(기준 6 .psv 어댑터는 test_psv_source.py 담당)
"""

from __future__ import annotations

import pytest

from sepsis import config as C
from sepsis.replay.engine import replay_stream

# --------------------------------------------------------------------------
# 가짜 협력자 (모델·서버·time.sleep·httpx 없이 — handoff §5:140)
# --------------------------------------------------------------------------

class FakeSource:
    """손으로 만든 dict 행 리스트 + patient_id 속성. __iter__로 행 yield (RowSource 구조)."""

    def __init__(self, patient_id: str, rows: list[dict]):
        self.patient_id = patient_id
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class SpySender:
    """send(patient_id, features) 호출을 받아적는 우체통. dict 반환 (Sender 구조)."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def send(self, patient_id: str, features: dict) -> dict:
        # features 의 얕은 복사로 호출 시점 상태를 박제(엔진이 dict 재사용/변형해도 증거 보존)
        self.calls.append((patient_id, dict(features)))
        return {"patient_id": patient_id, "p": 0.0, "alarm": False}


def make_sleep_recorder():
    """호출 인자를 기록하는 가짜 sleep. (recorded 리스트, sleep_fn) 반환."""
    recorded: list[float] = []

    def sleep_fn(seconds):
        recorded.append(seconds)

    return recorded, sleep_fn


VITALS = C.featureset_columns("vitals")  # ["HR","O2Sat","Temp","SBP","MAP","DBP","Resp","Age","Gender"]


def full_row(**overrides) -> dict:
    """vitals 풀셋 행. 기본 raw 값 후 overrides 적용."""
    base = {col: float(i + 1) for i, col in enumerate(VITALS)}
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# 기준 1 — 호출 수·순서 (F2 causal)   handoff §5.1:142
# --------------------------------------------------------------------------

def test_call_count_equals_rows():
    """T행 → 정확히 T번 send (건너뜀/중복 없음)."""
    rows = [full_row(HR=float(i)) for i in range(5)]
    sender = SpySender()
    _rec, sleep_fn = make_sleep_recorder()

    replay_stream(FakeSource("p000001", rows), sender, speed=3600.0, sleep_fn=sleep_fn)

    assert len(sender.calls) == len(rows) == 5


def test_call_order_preserved_no_skip_no_reorder():
    """전달된 행이 0,1,…,T-1 순서 그대로(역순/재배치 없음)."""
    rows = [full_row(HR=float(i)) for i in range(4)]
    sender = SpySender()
    _rec, sleep_fn = make_sleep_recorder()

    replay_stream(FakeSource("p000001", rows), sender, speed=3600.0, sleep_fn=sleep_fn)

    seen_hr = [feats["HR"] for (_pid, feats) in sender.calls]
    assert seen_hr == [0.0, 1.0, 2.0, 3.0]


def test_empty_source_no_calls_no_sleep():
    """행 0개 → send 0번, sleep 0번 (경계)."""
    sender = SpySender()
    rec, sleep_fn = make_sleep_recorder()

    out = replay_stream(FakeSource("p000001", []), sender, speed=3600.0, sleep_fn=sleep_fn)

    assert sender.calls == []
    assert rec == []
    assert out == []


def test_returns_collected_responses_in_order():
    """반환 = send 응답들의 리스트(검사용), 순서 보존 (handoff §3.1:86, §6:157)."""
    rows = [full_row() for _ in range(3)]
    sender = SpySender()
    _rec, sleep_fn = make_sleep_recorder()

    out = replay_stream(FakeSource("pX", rows), sender, speed=3600.0, sleep_fn=sleep_fn)

    assert isinstance(out, list)
    assert len(out) == 3
    assert all(r["patient_id"] == "pX" for r in out)


# --------------------------------------------------------------------------
# 기준 2 — 결측 = null (F1 0-fill 금지)   handoff §5.2:143
# --------------------------------------------------------------------------

def test_none_feature_stays_none_not_zero_or_mean():
    """source가 None 준 feature는 send features에서 None (0/평균으로 안 채움)."""
    rows = [full_row(SBP=None, Temp=None)]
    sender = SpySender()
    _rec, sleep_fn = make_sleep_recorder()

    replay_stream(FakeSource("p000001", rows), sender, speed=3600.0, sleep_fn=sleep_fn)

    _pid, feats = sender.calls[0]
    assert feats["SBP"] is None
    assert feats["Temp"] is None
    # 0 이나 평균으로 메우지 않았는지 명시 확인
    assert feats["SBP"] != 0.0
    assert feats["Temp"] != 0.0


def test_none_value_preserved_across_multiple_rows():
    """행마다 다른 결측 패턴이 그대로 보존(엔진이 채우지 않음)."""
    rows = [full_row(HR=None), full_row(HR=10.0), full_row(HR=None)]
    sender = SpySender()
    _rec, sleep_fn = make_sleep_recorder()

    replay_stream(FakeSource("p000001", rows), sender, speed=3600.0, sleep_fn=sleep_fn)

    seen = [feats["HR"] for (_pid, feats) in sender.calls]
    assert seen == [None, 10.0, None]


# --------------------------------------------------------------------------
# 기준 3 — 키 한정 (F3 알 수 없는 키 422)   handoff §5.3:144
# --------------------------------------------------------------------------

def test_sent_keys_subset_of_featureset_columns():
    """send features 키 집합 ⊆ C.featureset_columns(fs). featureset 밖 키 없음."""
    rows = [full_row()]
    sender = SpySender()
    _rec, sleep_fn = make_sleep_recorder()

    replay_stream(FakeSource("p000001", rows), sender, speed=3600.0, sleep_fn=sleep_fn)

    allowed = set(C.featureset_columns("vitals"))
    _pid, feats = sender.calls[0]
    assert set(feats.keys()).issubset(allowed)


def test_engine_does_not_inject_nonfeature_keys():
    """엔진은 source 행 키를 그대로 옮길 뿐 — 비-feature 키(SepsisLabel 등) 주입 안 함.

    source 행이 featureset 키만 담고 있으면, send features 에도 그 키만 있어야 한다
    (엔진이 patient_id/label 등을 features 안에 섞어넣지 않음 — F3 근거).
    """
    rows = [full_row()]
    sender = SpySender()
    _rec, sleep_fn = make_sleep_recorder()

    replay_stream(FakeSource("p000001", rows), sender, speed=3600.0, sleep_fn=sleep_fn)

    _pid, feats = sender.calls[0]
    forbidden = {"SepsisLabel", "ICULOS", "patient_id", "EtCO2"}
    assert forbidden.isdisjoint(set(feats.keys()))


# --------------------------------------------------------------------------
# 기준 4 — sleep 간격·speed 스케일 (F6)   handoff §5.4:145 / §3.1:89-94
# --------------------------------------------------------------------------

def test_sleep_count_is_T_minus_one():
    """T개 전송 → 정확히 T-1번 sleep (첫 행 앞 없음, 마지막 뒤 trailing 없음)."""
    rows = [full_row() for _ in range(5)]
    sender = SpySender()
    rec, sleep_fn = make_sleep_recorder()

    replay_stream(FakeSource("p000001", rows), sender, speed=3600.0, sleep_fn=sleep_fn)

    assert len(rec) == len(rows) - 1 == 4


def test_single_row_no_sleep():
    """행 1개 → sleep 0번 (행0은 즉시 전송, 앞에 sleep 없음). 경계."""
    rows = [full_row()]
    sender = SpySender()
    rec, sleep_fn = make_sleep_recorder()

    replay_stream(FakeSource("p000001", rows), sender, speed=3600.0, sleep_fn=sleep_fn)

    assert rec == []
    assert len(sender.calls) == 1


@pytest.mark.parametrize(
    "speed,expected_interval",
    [
        (3600.0, 1.0),   # 1시간을 1초로
        (7200.0, 0.5),
        (1.0, 3600.0),   # 실시간
    ],
)
def test_sleep_interval_equals_3600_over_speed(speed, expected_interval):
    """기록된 sleep 인자들이 모두 3600/speed."""
    rows = [full_row() for _ in range(4)]
    sender = SpySender()
    rec, sleep_fn = make_sleep_recorder()

    replay_stream(FakeSource("p000001", rows), sender, speed=speed, sleep_fn=sleep_fn)

    assert len(rec) == 3
    assert all(s == pytest.approx(expected_interval) for s in rec)


@pytest.mark.parametrize("bad_speed", [0.0, -1.0, -3600.0])
def test_speed_le_zero_raises_value_error(bad_speed):
    """speed<=0 → ValueError (F6: 무한/음수 대기 가드)."""
    rows = [full_row() for _ in range(3)]
    sender = SpySender()
    _rec, sleep_fn = make_sleep_recorder()

    with pytest.raises(ValueError):
        replay_stream(FakeSource("p000001", rows), sender, speed=bad_speed, sleep_fn=sleep_fn)


# --------------------------------------------------------------------------
# 기준 5 — patient_id 무상태 플러밍 (F5)   handoff §5.5:146
# --------------------------------------------------------------------------

def test_all_sends_carry_source_patient_id():
    """모든 send에 source.patient_id가 그대로 실림."""
    rows = [full_row() for _ in range(3)]
    sender = SpySender()
    _rec, sleep_fn = make_sleep_recorder()

    replay_stream(FakeSource("p042", rows), sender, speed=3600.0, sleep_fn=sleep_fn)

    assert all(pid == "p042" for (pid, _feats) in sender.calls)


def test_two_sources_same_sender_no_id_mixing():
    """서로 다른 두 source를 같은 fake sender로 각각 돌리면 각 호출 patient_id가 자기 source 것."""
    sender = SpySender()
    _rec, sleep_fn = make_sleep_recorder()

    src_a = FakeSource("pAAA", [full_row() for _ in range(2)])
    src_b = FakeSource("pBBB", [full_row() for _ in range(3)])

    replay_stream(src_a, sender, speed=3600.0, sleep_fn=sleep_fn)
    a_calls = list(sender.calls)
    replay_stream(src_b, sender, speed=3600.0, sleep_fn=sleep_fn)
    b_calls = sender.calls[len(a_calls):]

    assert [pid for (pid, _f) in a_calls] == ["pAAA", "pAAA"]
    assert [pid for (pid, _f) in b_calls] == ["pBBB", "pBBB", "pBBB"]


def test_engine_holds_no_patient_state_between_runs():
    """엔진은 무상태 — 같은 sender로 두 source를 돌려도 직전 환자 id가 새어들지 않음 (F5).

    두 번째 source의 send 호출 patient_id 가 첫 source 것으로 오염되면 실패.
    """
    sender = SpySender()
    _rec, sleep_fn = make_sleep_recorder()

    replay_stream(FakeSource("first", [full_row()]), sender, speed=3600.0, sleep_fn=sleep_fn)
    replay_stream(FakeSource("second", [full_row()]), sender, speed=3600.0, sleep_fn=sleep_fn)

    pids = [pid for (pid, _f) in sender.calls]
    assert pids == ["first", "second"]


# --------------------------------------------------------------------------
# 기준 7 — 정직성: 전처리 부재   handoff §5.7:148 / §2:44
# --------------------------------------------------------------------------

def test_no_preprocessing_raw_values_passed_through():
    """엔진 출력 = raw 값 또는 None만. ffill/clip/z-score 흔적 없음.

    - clip 범위(HR 0..300) 밖 값도 그대로 통과(엔진이 clip 안 함).
    - 결측은 직전 값으로 ffill 되지 않음(None 유지).
    """
    # HR 999.0 은 CLIP_BOUNDS["HR"]=(0,300) 밖 — 엔진이 clip 하면 300 으로 잘릴 것
    rows = [full_row(HR=999.0), full_row(HR=None)]
    sender = SpySender()
    _rec, sleep_fn = make_sleep_recorder()

    replay_stream(FakeSource("p000001", rows), sender, speed=3600.0, sleep_fn=sleep_fn)

    _pid0, f0 = sender.calls[0]
    _pid1, f1 = sender.calls[1]
    assert f0["HR"] == 999.0          # clip 안 함
    assert f1["HR"] is None           # ffill 안 함 (999.0 로 안 메움)
