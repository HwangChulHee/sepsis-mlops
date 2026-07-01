"""관측성 env 게이트 (arm-2 토글) 계약 테스트 — 2A 핸드오프 §A.

spec-writer 전용: **구현 코드(src/)를 보지 않고** §A만 보고 작성됐다. 게이트·XGB
부가계측 표면 구현은 §B(구현자) 소관이라 여기선 알 수 없으므로, 테스트는
conftest 의 seam(`_build_gated_client` / `_sample_request`)에만 의존한다. 미구현
상태에선 그 seam 이 명시적 NotImplementedError 를 던져 모든 테스트가 **'미구현'
단 하나의 이유로 결정론적 RED** 가 된다.

관측 인터페이스: 문서화된 운영 env `SEPSIS_SERVE_AUX_METRICS`
  - 미설정 / 1·true·on -> ON  (기본 = 배포 프로파일 arm-1, 부가계측 수행)
  - 0·false·off       -> OFF (순수 추론 프로파일 arm-2, 부가계측 없음)
  - 그 외 임의 문자열   -> ON  (관대한 파싱, 500 금지)

§A 성공기준 -> 테스트 대응:
  A0-대칭 / A0-대칭-표면 : test_a0_symmetry_surface_and_gate_present  (kind 파라미터화)
  A1-a (OFF→샘플 라인 부재): test_a1_gate_off_hides_feature_samples
  A1-b (ON→출현·증가)      : test_a1_gate_on_shows_and_increments_feature_samples
  A2-a (응답 불변)          : test_a2a_response_invariant_across_gate
  A2-b (상태 진행 불변)     : test_a2b_state_progression_invariant
  A3   (latency/req 유지)   : test_a3_latency_and_request_counters_survive_gate_off
  A4-a (미설정 → ON 기본)   : test_a4a_unset_default_is_on
  A4-b (알 수 없는 값 관대) : test_a4b_unknown_value_is_lenient_on
  A0/A4 (명시 OFF 값)       : test_a4_explicit_off_values_disable_aux
  A0   (명시 ON 값)         : test_a4_explicit_on_values_enable_aux

A0-대칭은 모든 테스트를 kind∈{"gru","xgb"} 로 파라미터화해 구현한다 — 한 kind만
게이트를 달고 다른 kind가 안 달았으면 그 kind 파라미터가 실패한다(load-bearing).
"""

from __future__ import annotations

import pytest

# 관측 env 이름 — §A A0 에 문자 단위로 명시된 운영 인터페이스.
AUX_ENV = "SEPSIS_SERVE_AUX_METRICS"

# A0-대칭: 두 서빙 인스턴스 각각에서 A1~A4 가 성립해야 한다.
KINDS = ["gru", "xgb"]

# 요청 횟수 상수.
N = 20      # A1/A4 부가계측 관측용 (§A 예: 20회)
N_LAT = 7   # A3 "정확히 N 증가" — 20과 다른 값이라 exactness 가 의미를 가짐
N_SEQ = 5   # A2-b 상태 진행 시퀀스 길이

# §A 에 명시된 부가계측 시계열 이름.
VALUE_COUNT = "serve_input_feature_value_count"      # 히스토그램 _count 샘플
MISSING_TOTAL = "serve_input_missing_total"          # 결측 카운터
LAT_COUNT = "serve_predict_latency_seconds_count"    # 서버 latency 히스토그램 _count
REQ_TOTAL = "serve_predict_requests_total"           # 요청 카운터

RESPONSE_KEYS = ("patient_id", "p", "alarm", "featureset")


# --------------------------------------------------------------------------
# 프로메테우스 텍스트 파싱 헬퍼
#
# §A 프로메테우스 주의: `# HELP`/`# TYPE` 헤더 라인은 게이트 OFF여도 항상
# 노출된다. OFF 에서 사라지는 것은 **라벨이 붙은 샘플 라인**뿐이다. 따라서
# 판정은 "이름 substring 부재"가 아니라 "feature= 라벨 샘플 라인의 부재/증감"
# 으로 한다. 아래 헬퍼는 항상 `#` 주석 라인을 건너뛴다.
# --------------------------------------------------------------------------


def _metrics_text(client) -> str:
    r = client.get("/metrics")
    assert r.status_code == 200, f"/metrics 가 {r.status_code} 응답 (기대 200)"
    return r.text


def _all_feature_lines(text: str) -> list[str]:
    """부가계측 표면의 `feature=` 라벨 샘플 라인 전체 (헤더 제외).

    `serve_input_feature_value_*`(bucket/count/sum) 와 `serve_input_missing_total`
    중 `feature=` 라벨이 붙은 샘플 라인만 모은다.
    """
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "feature=" not in s:
            continue
        if s.startswith("serve_input_feature_value") or s.startswith(MISSING_TOTAL):
            out.append(s)
    return out


def _samples(text: str, sample_name: str) -> dict[str, float]:
    """정확히 `sample_name` 인 샘플 라인 -> {"name{labels}": value}."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith(sample_name + "{") or s.startswith(sample_name + " "):
            key, _, val = s.rpartition(" ")
            try:
                out[key] = float(val)
            except ValueError:
                continue
    return out


def _feature_value_counts(text: str) -> dict[str, float]:
    """feature= 라벨이 붙은 `serve_input_feature_value_count` 샘플만."""
    return {k: v for k, v in _samples(text, VALUE_COUNT).items() if "feature=" in k}


def _scalar_total(text: str, sample_name: str) -> float:
    """`sample_name` 샘플 라인 값의 합(라벨이 여럿이면 합산). 없으면 0.0."""
    vals = _samples(text, sample_name)
    return sum(vals.values()) if vals else 0.0


def _post_n(client, kind: str, sample_request, n: int, patient_id: str):
    """유효 /predict 요청을 n회 보내고 응답 리스트를 반환. 각 응답 200 을 강제.

    step=i 로 같은 환자의 타임스텝을 진행시킨다(부가계측·상태 누적 유발).
    5xx 가 나면 여기서 실패 -> A4 '500 금지' 도 함께 지킨다.
    """
    resps = []
    for i in range(n):
        payload = sample_request(kind, patient_id=patient_id, step=i)
        r = client.post("/predict", json=payload)
        assert r.status_code == 200, f"/predict 가 {r.status_code} 응답: {r.text}"
        resps.append(r)
    return resps


# --------------------------------------------------------------------------
# A0-대칭 / A0-대칭-표면 (load-bearing)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_a0_symmetry_surface_and_gate_present(kind, gated_client, sample_request):
    """설계결정 A0-대칭 / A0-대칭-표면.

    두 서버(gru·xgb) **각각에서** 부가계측 표면이 동형으로 존재하고 게이트가
    대칭으로 얹혀야 한다: ON 기동 -> 피처 샘플 라인 관측됨, OFF 기동 -> 한 줄도
    안 나타남. 한 kind 만 성립하고 다른 kind 가 게이트/표면을 안 달았으면 그 kind
    파라미터가 실패한다(비대칭 세금으로 벤치 오염 방지).

    on/off 를 별도 인스턴스로 세워, OFF 가 ON 의 잔여 샘플에 오염되지 않음도
    함께 확인한다(seam 격리 계약).
    """
    on = gated_client(kind, env={AUX_ENV: "1"})
    off = gated_client(kind, env={AUX_ENV: "0"})

    _post_n(on, kind, sample_request, N, patient_id="on-A")
    _post_n(off, kind, sample_request, N, patient_id="off-A")

    assert _all_feature_lines(_metrics_text(on)), (
        f"{kind}: ON 인데 부가계측 표면(피처 샘플 라인)이 없음 — 표면 비대칭"
    )
    assert _all_feature_lines(_metrics_text(off)) == [], (
        f"{kind}: OFF 인데 피처 샘플 라인이 존재 — 게이트 미작동 또는 인스턴스 오염"
    )


# --------------------------------------------------------------------------
# A1. 게이트 OFF -> 부가계측 시계열이 사라진다 (arm-2)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_a1_gate_off_hides_feature_samples(kind, gated_client, sample_request):
    """설계결정 A1-a: OFF 기동 -> /predict N회 뒤에도 feature= 샘플 라인 0줄.

    헤더(# HELP/# TYPE)는 남을 수 있으나 라벨 샘플 라인은 한 줄도 없어야 한다.
    """
    off = gated_client(kind, env={AUX_ENV: "0"})

    before = _all_feature_lines(_metrics_text(off))
    assert before == [], f"{kind}: predict 전인데 이미 feature 샘플 라인 존재: {before}"

    _post_n(off, kind, sample_request, N, patient_id="pA")

    after = _all_feature_lines(_metrics_text(off))
    assert after == [], (
        f"{kind}: OFF 인데 {N}회 predict 후 feature 샘플 라인이 나타남 "
        f"(게이트가 부가계측을 끄지 않음): {after[:3]}..."
    )


@pytest.mark.parametrize("kind", KINDS)
def test_a1_gate_on_shows_and_increments_feature_samples(
    kind, gated_client, sample_request
):
    """설계결정 A1-b: ON 기동 -> feature= 샘플 라인이 나타나고 count 가 N 증가.

    강건성: N요청 전후 delta 로 검증한다 — 관측된 각 피처의
    serve_input_feature_value_count{feature=..} 가 정확히 N 증가.
    """
    on = gated_client(kind, env={AUX_ENV: "1"})

    base_counts = _feature_value_counts(_metrics_text(on))

    _post_n(on, kind, sample_request, N, patient_id="pA")

    after_text = _metrics_text(on)
    lines = _all_feature_lines(after_text)
    assert lines, f"{kind}: ON 인데 feature 샘플 라인이 하나도 없음 — 부가계측 표면 부재"

    after_counts = _feature_value_counts(after_text)
    assert after_counts, (
        f"{kind}: ON 인데 {VALUE_COUNT}{{feature=..}} 샘플이 없음"
    )
    for key, val in after_counts.items():
        delta = val - base_counts.get(key, 0.0)
        assert delta == N, (
            f"{kind}: {key} 의 count delta={delta}, 기대 {N} "
            f"(호출 수만큼 증가해야 함)"
        )


# --------------------------------------------------------------------------
# A2. 게이트는 예측/응답을 바꾸지 않는다 (격리 — load-bearing)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_a2a_response_invariant_across_gate(kind, gated_client, sample_request):
    """설계결정 A2-a: 같은 요청을 ON/OFF 서버에 보내면 응답 4키 값 완전 동일.

    p 는 부동소수 동일, alarm·featureset·patient_id 동일.
    """
    on = gated_client(kind, env={AUX_ENV: "1"})
    off = gated_client(kind, env={AUX_ENV: "0"})

    payload = sample_request(kind, patient_id="pA", step=0)
    r_on = on.post("/predict", json=payload)
    r_off = off.post("/predict", json=payload)
    assert r_on.status_code == 200, f"{kind} ON /predict {r_on.status_code}: {r_on.text}"
    assert r_off.status_code == 200, f"{kind} OFF /predict {r_off.status_code}: {r_off.text}"

    j_on, j_off = r_on.json(), r_off.json()
    for k in RESPONSE_KEYS:
        assert k in j_on and k in j_off, f"{kind}: 응답에 키 {k} 없음"
        assert j_on[k] == j_off[k], (
            f"{kind}: 키 {k} 가 ON({j_on[k]}) != OFF({j_off[k]}) — 게이트가 예측을 바꿈"
        )
    # p 부동소수 동일 (완전 동일 요구)
    assert float(j_on["p"]) == float(j_off["p"])


@pytest.mark.parametrize("kind", KINDS)
def test_a2b_state_progression_invariant(kind, gated_client, sample_request):
    """설계결정 A2-b: 같은 환자의 타임스텝 시퀀스를 ON/OFF 에 흘리면 p 시퀀스 동일.

    환자별 상태가 시퀀스에 걸쳐 누적되게 한 뒤(step 진행), 두 상태의 매 응답 p 가
    동일해야 한다 — 게이트가 예측에 쓰는 상태 진행을 건드리지 않음.
    """
    on = gated_client(kind, env={AUX_ENV: "1"})
    off = gated_client(kind, env={AUX_ENV: "0"})

    p_on: list[float] = []
    p_off: list[float] = []
    for i in range(N_SEQ):
        req = sample_request(kind, patient_id="seq", step=i)
        r_on = on.post("/predict", json=req)
        r_off = off.post("/predict", json=req)
        assert r_on.status_code == 200 and r_off.status_code == 200, (
            f"{kind}: 시퀀스 step={i} 응답 실패"
        )
        p_on.append(float(r_on.json()["p"]))
        p_off.append(float(r_off.json()["p"]))

    assert p_on == p_off, (
        f"{kind}: 상태 진행 p 시퀀스 불일치 ON={p_on} OFF={p_off} "
        f"(게이트가 상태 진행을 건드림)"
    )


# --------------------------------------------------------------------------
# A3. 서버 latency 히스토그램은 게이트와 무관하게 유지
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_a3_latency_and_request_counters_survive_gate_off(
    kind, gated_client, sample_request
):
    """설계결정 A3: OFF 여도 /predict N회 -> latency_count 정확히 N 증가,
    requests_total 도 N 증가.

    latency 관측은 '부가 계측'이 아니라 벤치의 핵심 지표이므로 arm-2 에서도 살아야
    한다.
    """
    off = gated_client(kind, env={AUX_ENV: "0"})

    before = _metrics_text(off)
    lat0 = _scalar_total(before, LAT_COUNT)
    req0 = _scalar_total(before, REQ_TOTAL)

    _post_n(off, kind, sample_request, N_LAT, patient_id="pA")

    after = _metrics_text(off)
    lat_delta = _scalar_total(after, LAT_COUNT) - lat0
    req_delta = _scalar_total(after, REQ_TOTAL) - req0

    assert lat_delta == N_LAT, (
        f"{kind}: OFF 인데 {LAT_COUNT} 증가분={lat_delta}, 기대 {N_LAT}"
    )
    assert req_delta == N_LAT, (
        f"{kind}: OFF 인데 {REQ_TOTAL} 증가분={req_delta}, 기대 {N_LAT}"
    )


# --------------------------------------------------------------------------
# A4. 실패/기본 모드
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_a4a_unset_default_is_on(kind, gated_client, sample_request, monkeypatch):
    """설계결정 A4-a: env 미설정 -> ON(배포 프로파일, 부가계측 켜짐).

    ambient 환경에 값이 남아있지 않도록 지운 뒤 env=None 으로 기동한다.
    """
    monkeypatch.delenv(AUX_ENV, raising=False)
    client = gated_client(kind, env=None)  # 현재(=지워진) 환경에서 기동

    _post_n(client, kind, sample_request, N, patient_id="pA")

    assert _all_feature_lines(_metrics_text(client)), (
        f"{kind}: env 미설정인데 부가계측 샘플 라인이 없음 — 기본값이 ON 이 아님"
    )


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("value", ["banana", "2", "yes-ish", "maybe"])
def test_a4b_unknown_value_is_lenient_on(
    kind, value, gated_client, sample_request
):
    """설계결정 A4-b: 알 수 없는 값 -> 500 없이 ON 유지(관대한 파싱).

    폐집합(0/false/off)에 없는 임의 문자열은 전부 ON 으로 해석되며, 서버가 500 으로
    죽지 않는다(_post_n 이 200 을 강제하므로 500 이면 여기서 실패).
    """
    client = gated_client(kind, env={AUX_ENV: value})

    _post_n(client, kind, sample_request, N, patient_id="pA")

    assert _all_feature_lines(_metrics_text(client)), (
        f"{kind}: 알 수 없는 값 {value!r} 는 ON 이어야 하는데 부가계측 샘플 라인 없음"
    )


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("value", ["0", "false", "off"])
def test_a4_explicit_off_values_disable_aux(
    kind, value, gated_client, sample_request
):
    """설계결정 A0/A4: 명시적 0·false·off -> OFF (피처 샘플 라인 부재)."""
    client = gated_client(kind, env={AUX_ENV: value})

    _post_n(client, kind, sample_request, N, patient_id="pA")

    assert _all_feature_lines(_metrics_text(client)) == [], (
        f"{kind}: {value!r} 는 OFF 여야 하는데 피처 샘플 라인이 존재"
    )


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("value", ["1", "true", "on"])
def test_a4_explicit_on_values_enable_aux(
    kind, value, gated_client, sample_request
):
    """설계결정 A0: 명시적 1·true·on -> ON (피처 샘플 라인 출현)."""
    client = gated_client(kind, env={AUX_ENV: value})

    _post_n(client, kind, sample_request, N, patient_id="pA")

    assert _all_feature_lines(_metrics_text(client)), (
        f"{kind}: {value!r} 는 ON 이어야 하는데 피처 샘플 라인이 없음"
    )
