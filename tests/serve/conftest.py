"""Test seams for the XGB minimal serving contract (1차 핸드오프 §A).

spec-writer 전용: 이 파일은 **구현 코드를 보지 않고** 작성됐다. XGB 서빙 앱의
모듈 경로·팩토리 시그니처(§B, 구현자 소관)는 여기서 알 수 없으므로,
테스트는 아래 seam(`_build_xgb_client`)에만 의존한다.

구현 단계에서 **main이 `_build_xgb_client` 하나만 실제 앱에 연결**하면
`xgb_client` / `xgb_client_labs` / `xgb_client_with_env` 세 fixture가 모두 살아난다.
그 전까지는 seam이 명시적으로 NotImplementedError를 던져, 모든 서빙-계약 테스트가
**'미구현' 단 하나의 이유로 결정론적 RED**가 되게 한다(구문/경로 오류가 아니라).
"""

from __future__ import annotations

import pytest


def _build_xgb_client(featureset: str, env: dict | None = None):
    """XGB 최소 서빙 앱에 대한 HTTP 테스트 클라이언트를 반환하는 seam.

    계약(구현자가 만족시켜야 하는 것):
      - featureset="vitals"     -> 9키 raw feature 서버
        featureset="vitals_labs"-> 18키 raw feature 서버
      - `env`가 주어지면 그 환경변수(예: SEPSIS_XGB_BEST_ITER_OVERRIDE) 아래에서
        앱을 기동한다. 무효값이면 기동 자체가 실패(예외)하거나, 반환된 클라이언트의
        /predict가 5xx를 내야 한다(§A3-b — 조용한 폴백 금지).
      - 반환 객체는 최소한 `.post(path, json=...)` 와 `.get(path)` 를 지원한다
        (FastAPI TestClient 또는 httpx 클라이언트).
      - 각 호출은 **버퍼가 비어있는 새 앱 인스턴스**를 준다(환자 버퍼 격리를 위해
        테스트 간 상태가 새지 않아야 한다 — fixture가 function-scoped인 이유).

    구현 단계(main): 아래처럼 실제 XGB 앱에 연결한다.
    """
    import os

    from fastapi.testclient import TestClient

    from sepsis.serve.xgb_app import build_app

    old: dict[str, str | None] = {}
    if env:
        for k, v in env.items():
            old[k] = os.environ.get(k)
            os.environ[k] = v
    try:
        app = build_app(featureset)   # 무효 override면 여기서 RuntimeError (A3-b 기동 실패)
    finally:
        if env:
            for k, prev in old.items():
                if prev is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = prev
    return TestClient(app)


@pytest.fixture
def xgb_client():
    """featureset=vitals(9키) XGB 최소 서빙 앱의 테스트 클라이언트."""
    return _build_xgb_client("vitals")


@pytest.fixture
def xgb_client_labs():
    """(선택) featureset=vitals_labs(18키) XGB 서빙 앱의 테스트 클라이언트."""
    return _build_xgb_client("vitals_labs")


@pytest.fixture
def xgb_client_with_env():
    """env를 받아 그 환경에서 앱을 기동하는 팩토리 fixture (§A3-b seam).

    사용: `client = xgb_client_with_env({"SEPSIS_XGB_BEST_ITER_OVERRIDE": "0"})`
    무효 override면 이 호출이 예외를 던지거나(기동 실패), 반환된 클라이언트의
    /predict가 5xx를 낸다. 정상(override 미설정)이면 골든을 재현한다.
    """

    def _factory(env: dict | None = None, featureset: str = "vitals"):
        return _build_xgb_client(featureset, env=env)

    return _factory


# ===========================================================================
# 관측성 게이트 2A §A — 새 seam (arm-2 토글).
# 기존 1차 seam(`_build_xgb_client`)은 건드리지 않고, 게이트 계약 전용 seam만
# 추가한다. 이 seam들은 미구현 상태에서 **명시적 NotImplementedError**로 RED를
# 낸다 — 모듈 경로·격리 방식은 §B/main 소관이라 여기서 하드코딩하지 않는다.
# ===========================================================================


def _build_gated_client(kind: str, env: dict | None = None):
    """관측성 게이트가 얹힌 서빙 앱의 테스트 클라이언트를 반환하는 seam (2A §A).

    kind:
      - "gru" -> 기존 GRU 서빙 앱(부가계측 표면 존재, 그 위에 게이트만 얹힘)
      - "xgb" -> XGB 최소 서빙 앱(+2A가 구축하는 부가계측 표면 + 게이트)

    env:
      - dict 이면 그 환경변수 아래에서 앱을 기동한다
        (예: {"SEPSIS_SERVE_AUX_METRICS": "0"} → 부가계측 OFF).
      - None 이면 **현재 프로세스 환경(os.environ 그대로)** 아래에서 기동한다.
        A4-a(안전한 기본값) 테스트가 monkeypatch로 해당 env를 지운 뒤 이 경로로
        "미설정 → ON" 을 검증한다.

    ★ 격리 계약 (이 핸드오프의 핵심 테스트 인프라 요구 — 반드시 지킬 것):
      각 호출은 **부가계측 관측이 이전 인스턴스와 완전히 격리된 깨끗한 인스턴스**를
      돌려줘야 한다. 구체적으로 각 인스턴스는 자신만의 fresh 프로메테우스 레지스트리
      (또는 프로세스 격리)를 가져야 하며, 한 인스턴스의 /predict 가 남긴
      `serve_input_feature_value_*{feature=...}` / `serve_input_missing_total{feature=...}`
      샘플 라인이 **다른 인스턴스의 /metrics 로 새어 들어오면 안 된다**.
      이 격리가 없으면 A1-a(OFF → 피처 샘플 라인 0줄)가 직전 ON 인스턴스의 잔여
      전역-레지스트리 샘플에 오염돼 **거짓 실패**한다. main이 구현 단계에서 이
      격리(인스턴스별 fresh 레지스트리)를 포함해 seam을 실제 앱에 연결한다.

    반환 객체는 최소한 `.post(path, json=...)` 와 `.get(path)` 를 지원한다
    (FastAPI TestClient 또는 httpx 클라이언트). /metrics·/predict 경로를 노출한다.
    """
    raise NotImplementedError(
        "관측성 게이트 서빙 seam 미구현(2A §B): main이 kind∈{'gru','xgb'} 서빙 앱을 "
        "인스턴스별 fresh(격리된) 부가계측 레지스트리와 함께 연결해야 한다."
    )


def _sample_request(kind: str, patient_id: str = "obs-gate", step: int = 0) -> dict:
    """kind 에 맞는 유효한 /predict 요청 payload 1건을 돌려주는 seam.

    계약:
      - 같은 (kind, patient_id, step) 은 항상 **동일한 payload** 를 준다
        (A2 응답 불변 검증이 결정성에 의존).
      - `step` 은 같은 환자의 타임스텝을 진행시켜 상태 누적을 유발한다(A2-b).
      - 구체 피처 세트(GRU featureset / XGB 9·18키)는 각 서버 핸드오프 소관이라
        여기서 하드코딩하지 않는다 — main이 kind 에 맞는 실제 payload 를 채운다.
        (§A는 A1 판정을 "어떤 유효 요청이든 부가계측이 관측되는가"로 두므로
         payload 세부보다 게이트 동작이 핵심이다.)

    미구현: NotImplementedError.
    """
    raise NotImplementedError(
        "샘플 /predict payload seam 미구현(2A §B): main이 kind 에 맞는 유효 요청을 "
        "채워야 한다 (GRU featureset / XGB 9·18키)."
    )


@pytest.fixture
def gated_client():
    """관측성 게이트 서빙 클라이언트 팩토리 fixture (function-scoped 격리).

    사용: `client = gated_client("xgb", env={"SEPSIS_SERVE_AUX_METRICS": "0"})`
    """

    def _make(kind: str, env: dict | None = None):
        return _build_gated_client(kind, env=env)

    return _make


@pytest.fixture
def sample_request():
    """kind 에 맞는 유효 /predict payload 팩토리 fixture.

    사용: `payload = sample_request("gru", patient_id="pA", step=3)`
    """

    def _make(kind: str, patient_id: str = "obs-gate", step: int = 0) -> dict:
        return _sample_request(kind, patient_id=patient_id, step=step)

    return _make
