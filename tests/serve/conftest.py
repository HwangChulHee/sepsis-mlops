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
