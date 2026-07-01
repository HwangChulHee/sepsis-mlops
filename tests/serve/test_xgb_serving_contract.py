"""XGB 최소 서빙 1차 §A TDD — 구현 전 RED.

출제자-응시자 분리: 이 테스트는 `docs/design/serving-benchmark/handoff.md` **§A만**
보고 작성됐다. 구현 코드(src/, 기존 serve 앱)는 보지 않았다. 모든 검증은
§A가 명시한 **관측 가능한 입출력 행동 + 출제자가 박아둔 골든 상수**로만 한다.

앱 모듈 경로/팩토리는 §B(구현자) 소관이므로, 클라이언트는 conftest의
`_build_xgb_client` seam(fixture)으로만 얻는다. 구현 전에는 seam이
NotImplementedError를 던져 모든 테스트가 '미구현'이라는 단 하나의 이유로 RED.

추적: 각 테스트는 대응하는 §A 성공기준을 주석("설계 결정 …")으로 표기.
"""

from __future__ import annotations

import pytest

# --- §A3 골든 시퀀스 S_vitals (featureset=vitals, 9키, 5행) ---------------------
S_VITALS = [
    {"HR": 80, "O2Sat": 98, "Temp": 37.0, "SBP": 120, "MAP": 85, "DBP": 70, "Resp": 16, "Age": 64, "Gender": 1},  # noqa: E501
    {"HR": 88, "O2Sat": 97, "Temp": 37.2, "SBP": 118, "MAP": 83, "DBP": 68, "Resp": 18, "Age": 64, "Gender": 1},  # noqa: E501
    {"HR": 95, "O2Sat": 96, "Temp": 37.6, "SBP": 110, "MAP": 78, "DBP": 64, "Resp": 20, "Age": 64, "Gender": 1},  # noqa: E501
    {"HR": 104, "O2Sat": 94, "Temp": 38.1, "SBP": 102, "MAP": 72, "DBP": 60, "Resp": 22, "Age": 64, "Gender": 1},  # noqa: E501
    {"HR": 112, "O2Sat": 93, "Temp": 38.5, "SBP": 98, "MAP": 70, "DBP": 58, "Resp": 24, "Age": 64, "Gender": 1},  # noqa: E501
]
# 마지막 행 X (A2-a에서 단독/이력후 두 경로로 쓰임)
X_ROW = S_VITALS[-1]

# --- §A3 (선택) 골든 시퀀스 S_labs (featureset=vitals_labs, 명시 키만; 나머지 lab=부재=NaN) ---
S_LABS = [
    {"HR": 80, "O2Sat": 98, "Temp": 37.0, "SBP": 120, "MAP": 85, "DBP": 70, "Resp": 16, "Age": 64, "Gender": 1, "WBC": 8.0, "Lactate": 1.2, "Creatinine": 0.9},  # noqa: E501
    {"HR": 88, "O2Sat": 97, "Temp": 37.2, "SBP": 118, "MAP": 83, "DBP": 68, "Resp": 18, "Age": 64, "Gender": 1},  # noqa: E501
    {"HR": 95, "O2Sat": 96, "Temp": 37.6, "SBP": 110, "MAP": 78, "DBP": 64, "Resp": 20, "Age": 64, "Gender": 1, "Lactate": 2.1},  # noqa: E501
    {"HR": 104, "O2Sat": 94, "Temp": 38.1, "SBP": 102, "MAP": 72, "DBP": 60, "Resp": 22, "Age": 64, "Gender": 1, "WBC": 13.5},  # noqa: E501
    {"HR": 112, "O2Sat": 93, "Temp": 38.5, "SBP": 98, "MAP": 70, "DBP": 58, "Resp": 24, "Age": 64, "Gender": 1, "Lactate": 3.4, "Creatinine": 1.4},  # noqa: E501
]

# --- §A 골든 관측 상수 (출제자가 동결 아티팩트로 산출; 하드코딩) ---------------------
P_X_SINGLE = 0.64867   # A2-a(i): X 단독(첫 요청)
P_SEQ_5TH = 0.70981    # A2-a(ii)/A3-a: S_vitals 5행 후 5번째
P_SEQ_3RD = 0.24634    # A2-c/A3-a-mid: S_vitals 3번째
P_LABS_5TH = 0.83356   # A3-a(선택): S_labs 5행 후 5번째
EPS = 1e-4

EXPECTED_KEYS = {"patient_id", "p", "alarm", "featureset"}

BEST_ITER_ENV = "SEPSIS_XGB_BEST_ITER_OVERRIDE"


# --- helpers ------------------------------------------------------------------
def _predict(client, patient_id: str, features: dict):
    return client.post("/predict", json={"patient_id": patient_id, "features": features})


def _send_seq(client, patient_id: str, rows):
    """rows를 순서대로 /predict. 각 응답의 파싱된 JSON 리스트 반환."""
    out = []
    for row in rows:
        r = _predict(client, patient_id, row)
        assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
        out.append(r.json())
    return out


def _metric_count(client) -> tuple[bool, float]:
    """/metrics에서 serve_predict_latency_seconds_count 총합을 파싱.

    반환 (found, total). 라벨(featureset 등)이 붙어 여러 샘플이면 합산.
    HELP/TYPE 주석 라인(# 로 시작)은 제외.
    """
    r = client.get("/metrics")
    assert r.status_code == 200, f"/metrics not 200: {r.status_code}"
    total = 0.0
    found = False
    for line in r.text.splitlines():
        if line.startswith("#"):
            continue
        if line.startswith("serve_predict_latency_seconds_count"):
            found = True
            total += float(line.rsplit(" ", 1)[-1])
    return found, total


# ==============================================================================
# A1. /predict 계약 (GRU와 동일)
# ==============================================================================
def test_A1_predict_contract_exactly_four_keys(xgb_client):
    """설계 결정 A1 — 응답은 정확히 네 키, patient_id echo, 타입, featureset=vitals."""
    r = _predict(xgb_client, "p_a1", S_VITALS[0])
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    # 정확히 네 키 — 더도 덜도 아님
    assert set(body.keys()) == EXPECTED_KEYS, f"keys must be exactly {EXPECTED_KEYS}, got {set(body.keys())}"  # noqa: E501
    assert body["patient_id"] == "p_a1"
    assert isinstance(body["p"], (int, float)) and 0.0 <= body["p"] <= 1.0
    assert isinstance(body["alarm"], bool)
    assert body["featureset"] == "vitals"


def test_A1_gru_style_request_unmodified_returns_200(xgb_client):
    """설계 결정 A1 — GRU 서빙에 보내던 단일-timestep 요청을 수정 없이 보내도 200+네 키.

    §A1 예시 요청 스키마 {patient_id, features(단일 행)} 그대로 전송 → 422/500 없이 200.
    """
    gru_style = {"patient_id": "p000001", "features": {"HR": 88.0, "O2Sat": 97.0}}
    r = xgb_client.post("/predict", json=gru_style)
    assert r.status_code == 200, f"GRU-style request must yield 200, got {r.status_code}: {r.text}"  # noqa: E501
    assert set(r.json().keys()) == EXPECTED_KEYS


def test_A1_missing_value_null_or_absent_is_nan_not_zerofill(xgb_client):
    """설계 결정 A1 (+ CLAUDE.md 누수방지) — 값 null 또는 키 부재는 결측(NaN) 허용, 0-fill 아님.

    관측: null 값 요청과 키-부재 요청 모두 422 없이 200 (서버가 결측을 받아들임).
    '0으로 채우지 않음'의 수치 교차검증은 A2-c/A3-a 골든 재현이 담당(여기선 수용성만).
    """
    with_null = {"patient_id": "p_a1n", "features": dict(S_VITALS[0], O2Sat=None)}
    r1 = xgb_client.post("/predict", json=with_null)
    assert r1.status_code == 200, f"null value must be accepted (NaN), got {r1.status_code}: {r1.text}"  # noqa: E501

    partial = {"patient_id": "p_a1m", "features": {"HR": 90.0, "Resp": 20}}
    r2 = xgb_client.post("/predict", json=partial)
    assert r2.status_code == 200, f"absent keys must be accepted (NaN), got {r2.status_code}: {r2.text}"  # noqa: E501


# ==============================================================================
# A2. 환자별 상태 = lookback 버퍼 (stateless 아님)
# ==============================================================================
def test_A2a_state_same_input_differs_by_history(xgb_client):
    """설계 결정 A2-a — 같은 행 X라도 이력 유무로 출력이 갈린다(반드시 다르다).

    (i) X 단독(새 환자 첫 요청)   -> p ≈ 0.64867
    (ii) S_vitals 5행 후 마지막 X -> p ≈ 0.70981
    두 값이 같으면 상태 미기억 = FAIL.
    """
    # (i) 단독
    r_single = _predict(xgb_client, "p_a2a_solo", X_ROW)
    assert r_single.status_code == 200, r_single.text
    p_single = r_single.json()["p"]
    assert abs(p_single - P_X_SINGLE) <= EPS, f"solo X: expected {P_X_SINGLE}, got {p_single}"

    # (ii) 이력 후
    seq = _send_seq(xgb_client, "p_a2a_seq", S_VITALS)
    p_seq = seq[-1]["p"]
    assert abs(p_seq - P_SEQ_5TH) <= EPS, f"seq 5th: expected {P_SEQ_5TH}, got {p_seq}"

    # 반드시 다르다 (약 0.061 차 ≫ eps)
    assert abs(p_seq - p_single) > 1e-3, (
        f"history must change output: solo={p_single} vs seq={p_seq} — server not remembering state"  # noqa: E501
    )


def test_A2b_patient_isolation_no_cross_contamination(xgb_client):
    """설계 결정 A2-b — P의 버퍼가 Q 요청에 오염되지 않는다.

    P에게 S_vitals를 보내되 매 행 사이에 Q에게 다른 행을 끼워 넣어도,
    P의 5번째 응답 = 0.70981 ± 1e-4 (A2-a(ii)와 동일).
    """
    noise_rows = list(reversed(S_VITALS))  # Q에게 줄 임의의 다른 이력
    p_resps = []
    for i, row in enumerate(S_VITALS):
        rp = _predict(xgb_client, "p_iso_P", row)
        assert rp.status_code == 200, rp.text
        p_resps.append(rp.json()["p"])
        # 사이에 Q 요청 끼워넣기 (P 버퍼를 오염시키면 안 됨)
        rq = _predict(xgb_client, "p_iso_Q", noise_rows[i])
        assert rq.status_code == 200, rq.text
    assert abs(p_resps[4] - P_SEQ_5TH) <= EPS, (
        f"P 5th under interleave: expected {P_SEQ_5TH}, got {p_resps[4]} — Q leaked into P buffer"  # noqa: E501
    )


def test_A2c_short_history_valid_and_not_zerofilled(xgb_client):
    """설계 결정 A2-c — 이력이 짧은 초기 요청도 500 없이 유효 p, 0-fill 아님.

    관측 교차검증: 3번째 응답 p ≈ 0.24634 (부족분을 학습과 동일한 결측 처리로 산출한 골든).
    서버가 부족분을 0으로 채우면 이 값과 불일치 → FAIL.
    """
    seq = _send_seq(xgb_client, "p_a2c", S_VITALS)  # 각 응답 200 보장(500 없음)
    for i, body in enumerate(seq):
        assert 0.0 <= body["p"] <= 1.0, f"short-history req#{i+1} invalid p={body['p']}"
    assert abs(seq[2]["p"] - P_SEQ_3RD) <= EPS, (
        f"3rd response: expected {P_SEQ_3RD} (short history, not 0-filled), got {seq[2]['p']}"
    )


def test_A2d_skew_guard_one_row_only_summary_fails(xgb_client):
    """설계 결정 A2-d (실패 케이스 필수) — 이력 없이 매 요청 1행으로만 요약하는 퇴화 구현을 잡는다.

    5행 시퀀스의 마지막 p(0.70981)는 동일 행 X를 1행 단독으로 요약한 값(0.64867)과
    반드시 다르다. 서버가 이력을 안 쌓고 1행만 쓰면 마지막 p가 0.64867에 착지 → 골든 불일치 FAIL.
    """
    r_solo = _predict(xgb_client, "p_a2d_solo", X_ROW)
    assert r_solo.status_code == 200, r_solo.text
    p_solo = r_solo.json()["p"]

    seq = _send_seq(xgb_client, "p_a2d_seq", S_VITALS)
    p_last = seq[-1]["p"]

    # 골든(이력 반영)과 일치해야 하고, 1행-only 요약값과는 달라야 한다.
    assert abs(p_last - P_SEQ_5TH) <= EPS, f"seq last: expected {P_SEQ_5TH}, got {p_last}"
    assert abs(p_last - p_solo) > 1e-3, (
        f"1-row-only degeneration not caught: last={p_last} == solo={p_solo} (train-serve skew)"
    )


# ==============================================================================
# A3. 챔피언 재현 골든 (best_iter 절단)
# ==============================================================================
def test_A3a_golden_reproduction_vitals(xgb_client):
    """설계 결정 A3-a — S_vitals 5행 후 5번째 p = 0.70981 ± 1e-4, alarm=true.

    비절단(전체 트리)이면 ≈0.69233 (gap ~0.0175 ≫ eps)이라 골든 불일치로 잡힌다.
    """
    seq = _send_seq(xgb_client, "p_a3a", S_VITALS)
    body = seq[-1]
    assert abs(body["p"] - P_SEQ_5TH) <= EPS, f"5th p: expected {P_SEQ_5TH}, got {body['p']}"
    assert body["alarm"] is True, f"alarm must be true at p={body['p']} (frozen tau)"


def test_A3a_mid_trajectory_fixed_vitals(xgb_client):
    """설계 결정 A3-a-mid (거짓 통과 방지) — 3번째 응답 p = 0.24634 ± 1e-4, alarm=false.

    마지막 값 하나만 대조하면 우연히 골든에 착지해 거짓 통과 가능 → 궤적 중간도 고정.
    """
    seq = _send_seq(xgb_client, "p_a3amid", S_VITALS)
    body = seq[2]
    assert abs(body["p"] - P_SEQ_3RD) <= EPS, f"3rd p: expected {P_SEQ_3RD}, got {body['p']}"
    assert body["alarm"] is False, f"alarm must be false at p={body['p']} (frozen tau)"


def test_A3a_tau_threshold_consistency(xgb_client):
    """설계 결정 A1/A3-a — alarm == (p >= tau)의 임계 일관성.

    §A는 tau 수치를 주지 않으나 골든 앵커로 관계를 검증:
    p=0.24634 -> alarm false, p=0.70981 -> alarm true. 즉 tau ∈ (0.24634, 0.70981].
    낮은 p가 true인데 높은 p가 false인 반전이 있으면 threshold 계약 위반.
    """
    seq = _send_seq(xgb_client, "p_a3tau", S_VITALS)
    low = seq[2]   # p≈0.24634
    high = seq[4]  # p≈0.70981
    assert low["alarm"] is False
    assert high["alarm"] is True
    # 단조성: 더 높은 p가 알람이면 더 낮은 p가 알람이 아닌 반전은 없어야
    pairs = [(b["p"], b["alarm"]) for b in seq]
    for pa, aa in pairs:
        for pb, ab in pairs:
            if pa < pb and aa is True:
                assert ab is True, f"threshold inversion: p={pa} alarm but p={pb} not-alarm"


def test_A3a_golden_reproduction_labs_optional(xgb_client_labs):
    """설계 결정 A3-a(선택) — S_labs 5행 후 5번째 p = 0.83356 ± 1e-4 (vitals_labs 18키).

    비절단 시 ≈0.82285 (gap 0.0107 ≫ eps). 나머지 lab 키 부재=NaN(0-fill 아님).
    """
    seq = _send_seq(xgb_client_labs, "p_a3labs", S_LABS)
    body = seq[-1]
    assert body["featureset"] == "vitals_labs"
    assert abs(body["p"] - P_LABS_5TH) <= EPS, f"labs 5th p: expected {P_LABS_5TH}, got {body['p']}"  # noqa: E501


@pytest.mark.parametrize("bad_value", ["0", "-1", "none"])
def test_A3b_invalid_best_iter_fails_observably(xgb_client_with_env, bad_value):
    """설계 결정 A3-b (실패 케이스 필수) — 무효 best_iter override는 관측 가능하게 실패.

    SEPSIS_XGB_BEST_ITER_OVERRIDE=0/음수/none 으로 기동하면:
      기동 실패(예외) 또는 /predict 5xx/에러. 200+전체트리 무성 폴백이면 FAIL.
    """
    try:
        client = xgb_client_with_env({BEST_ITER_ENV: bad_value})
    except NotImplementedError:
        raise  # 아직 미구현 -> RED (거짓 통과 방지: 이걸 '기동 실패'로 착각하지 않는다)
    except Exception:
        return  # 기동 자체가 관측 가능하게 실패 = 허용된 실패 경로 (PASS)

    # 기동에 성공했다면 /predict가 5xx로 관측 가능하게 실패해야 한다.
    r = _predict(client, "p_a3b", S_VITALS[0])
    assert r.status_code >= 500, (
        f"invalid best_iter must fail observably (5xx), got {r.status_code}: "
        "silent 200 full-tree fallback is a FAIL"
    )


def test_A3b_unset_override_reproduces_golden(xgb_client_with_env):
    """설계 결정 A3-b (정상 경로) — override 미설정이면 아티팩트 임베드값으로 골든 재현.

    빈 env(=override 미설정)로 기동한 서버가 S_vitals 5번째 = 0.70981을 재현.
    """
    client = xgb_client_with_env({})
    seq = _send_seq(client, "p_a3b_ok", S_VITALS)
    assert abs(seq[-1]["p"] - P_SEQ_5TH) <= EPS, (
        f"unset override must reproduce golden {P_SEQ_5TH}, got {seq[-1]['p']}"
    )


# ==============================================================================
# A4. latency 계측 존재·발동 (GRU와 대칭)
# ==============================================================================
def test_A4a_latency_metric_registered(xgb_client):
    """설계 결정 A4-a — /metrics에 serve_predict_latency_seconds_{count,bucket,sum} 존재."""
    r = xgb_client.get("/metrics")
    assert r.status_code == 200, f"/metrics not 200: {r.status_code}"
    text = r.text
    assert "serve_predict_latency_seconds_count" in text, "missing _count series"
    assert "serve_predict_latency_seconds_bucket" in text, "missing histogram _bucket series"
    assert "serve_predict_latency_seconds_sum" in text, "missing _sum series"


def test_A4b_latency_count_increments_exactly_n(xgb_client):
    """설계 결정 A4-b — /predict N회 성공 호출 → _count가 정확히 N 증가."""
    found_before, before = _metric_count(xgb_client)
    assert found_before, "serve_predict_latency_seconds_count must exist before calls"

    n = 3
    for i in range(n):
        r = _predict(xgb_client, "p_a4b", S_VITALS[i % len(S_VITALS)])
        assert r.status_code == 200, r.text

    found_after, after = _metric_count(xgb_client)
    assert found_after
    assert after - before == pytest.approx(n), (
        f"count must increase by exactly {n}: before={before}, after={after}"
    )
