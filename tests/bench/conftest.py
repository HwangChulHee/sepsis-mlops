"""Seam(주입 경계) 정의 — 벤치 하니스 2b §A TDD.

출제자-응시자 분리 원칙(spec-writer는 src를 보지 않는다)에 따라, 이 파일은
main(구현자)이 구현 단계에서 실제 조립/컷 함수에 **연결**할 두 개의 seam을 정의한다.
지금은 두 seam 모두 명시적으로 ``NotImplementedError``를 던져 **결정론적 RED**를 만든다.

핵심 이해(§A 입력 주입 계약, M-2):
    spec-writer는 라이브 서버·라이브 관측성 게이트(arm-2 토글)를 구동하지 않는다.
    arm-1/arm-2의 **원시 측정치**(client 벽시계 배열, server latency 요청별 계열,
    /proc RSS 값, throughput 카운트, featureset별 RSS, 비용 입력)를 하니스에
    **주입되는 알려진 입력**으로 주고, 하니스의 **집계·귀인·라벨링 로직**
    (주입값 → BenchResult 필드)만 검증한다. 즉 순수 집계 함수를 테스트한다.

주입 입력 계약(main이 이 형태 그대로 조립 함수를 만든다):

    injected = {
      "gru": {
        "arm1": {                     # 부가 계측 ON (배포 계측 프로파일)
          "client": [float, ...],     # (i) client 벽시계 배열 — 요청별 1값
          "server": [float, ...],     # (ii) server latency 요청별 계열
                                       #      (= _sum 스크레이프 인접 델타 배열,
                                       #       client 배열과 동일 길이·동일 요청집합, B-R3-1)
          "server_quantiles": {"p50","p95","p99"},  # (선택) 히스토그램 버킷보간 분위수.
                                       #      생략 시 server 계열에서 유도. 존재 시 arm.server 분위수로 사용.
                                       #      → A9 버킷 무관 검증용(서로 다른 버킷 해상도 주입).
          "rss": float, "peak": float,  # (iii) /proc RSS·peak 값
        },
        "arm2": {... 동일 형태 ...},    # 부가 계측 OFF (순수 추론 프로파일)
        "control9": {"rss": float},     # GRU/vitals9 통제 arm RSS (GRU는 배포==통제 동일 featureset)
        "throughput": {"n_streams": int, "patient_ids": [str, ...],
                       "req_per_sec": float, "wall_seconds": float},
        "boot_latency": float,          # 부팅 비용(모델 로드 + GRU 캘리브레이션)
      },
      "xgb": {... 동일 형태 ..., "control9": {"rss": float}},  # XGB/vitals9 통제(배포 featureset과 다름)
      "cost": {
        "target_throughput": float, "per_instance_throughput": float,
        "price_per_hr": float, "instance_type": str, "price_source": str,
      },
    }

집계 함수(_assemble_bench_result)는 이 dict를 받아 A0 스키마의 BenchResult를 조립해
돌려준다. 내부 정상상태 컷(K·threshold)은 하니스 상수(§B) 소관이므로 여기서 주입하지
않는다 — 골든은 index 0 이후 **상수 구간** 배열로 구성해 어떤 K에도 무관하게 성립시킨다.
"""

from __future__ import annotations

import copy

import pytest

# --- seam 상수 ------------------------------------------------------------
_SPIKE = 999.0     # index 0 워밍업 스파이크(무조건 제외 대상, A5-c 1단계)
_N = 60            # 상수 구간 길이(어떤 합리적 K에도 정상상태 창이 잡히도록 충분히 김)


def _const_series(value: float, n: int = _N, spike: float = _SPIKE) -> list[float]:
    """index 0 = 워밍업 스파이크, index 1..n-1 = 상수.

    상수 구간이라 정상상태 컷이 어디에 떨어지든(K 무관) 슬라이스 평균 == value.
    → 골든 산술(client_mean·server_mean·residual·tax)이 컷 구현 세부에 무관.
    """
    arr = [float(value)] * n
    arr[0] = float(spike)
    return arr


# ---------------------------------------------------------------------------
# SEAM 1: BenchResult 조립 함수
# main이 구현 단계에서 실제 조립 함수(모듈 경로는 §B 소관)에 연결한다.
# ---------------------------------------------------------------------------
def _assemble_bench_result(injected: dict):
    """주입된 원시 측정치(dict)를 받아 A0 스키마의 BenchResult를 조립해 돌려준다.

    RED: 미구현 상태에서는 NotImplementedError → 결정론적 RED.
    main은 이 함수 본문을 실제 조립 함수 호출로 교체한다.
    """
    raise NotImplementedError(
        "spec-writer seam: main이 벤치 하니스의 실제 BenchResult 조립 함수에 연결한다"
    )


# ---------------------------------------------------------------------------
# SEAM 2: 정상상태 컷 (보조 seam)
# main이 실제 컷 함수에 연결. 시그니처: (latency_array, k, threshold) -> (start:int, ok:bool)
#   - k·threshold를 명시 인자로 노출해 spec-writer가 경계 산술을 손으로 고정할 수 있게 한다.
#   - driving 배열은 client 벽시계 계열(m-R4-1).
# ---------------------------------------------------------------------------
def _steady_state_cut(latency_array, k, threshold):
    """latency 배열의 정상상태 시작 index와 수렴 여부를 돌려준다.

    반환 (start, ok):
      - 수렴 시 (steady_state_start>=1, True) — index 0 무조건 제외(A5-c 1단계)
      - 비수렴 시 (-1, False) — run FAIL(A5-c 3단계)

    RED: 미구현 상태에서는 NotImplementedError.
    """
    raise NotImplementedError(
        "spec-writer seam: main이 하니스의 실제 정상상태 컷 함수에 연결한다"
    )


# ---------------------------------------------------------------------------
# fixtures — seam 콜러블과 표준 주입 입력을 노출
# ---------------------------------------------------------------------------
@pytest.fixture
def assemble():
    return _assemble_bench_result


@pytest.fixture
def steady_cut():
    return _steady_state_cut


@pytest.fixture
def const_series():
    return _const_series


def _canonical_injected() -> dict:
    """골든 주입 입력.

    유도 골든(손 계산):
      gru.arm1 residual = 12 - 5 = 7 ; gru.arm2 residual = 10 - 5 = 5 ; gru.tax = 2
      xgb.arm1 residual = 20 - 8 = 12 ; xgb.arm2 residual = 17 - 8 = 9 ; xgb.tax = 3
      gru.memory.instrumentation = 200 - 150 = 50
      xgb.memory.instrumentation = 320 - 300 = 20
      cost.instance_count = ceil(100 / 30) = 4 ; cost.cost_per_hr = 4 * 0.5 = 2.0
    """
    return {
        "gru": {
            "arm1": {
                "client": _const_series(12.0),
                "server": _const_series(5.0),
                "rss": 200.0,
                "peak": 260.0,
            },
            "arm2": {
                "client": _const_series(10.0),
                "server": _const_series(5.0),
                "rss": 150.0,
                "peak": 190.0,
            },
            "control9": {"rss": 200.0},  # GRU 배포==통제(vitals9) → gru9.memory.rss == gru.memory.rss
            "throughput": {
                "n_streams": 8,
                "patient_ids": [f"p{i}" for i in range(8)],
                "req_per_sec": 42.0,
                "wall_seconds": 3.0,
            },
            "boot_latency": 1.5,
        },
        "xgb": {
            "arm1": {
                "client": _const_series(20.0),
                "server": _const_series(8.0),
                "rss": 320.0,
                "peak": 400.0,
            },
            "arm2": {
                "client": _const_series(17.0),
                "server": _const_series(8.0),
                "rss": 300.0,
                "peak": 360.0,
            },
            "control9": {"rss": 250.0},  # XGB/vitals9 통제(배포 featureset과 다름)
            "throughput": {
                "n_streams": 8,
                "patient_ids": [f"q{i}" for i in range(8)],
                "req_per_sec": 30.0,
                "wall_seconds": 4.0,
            },
            "boot_latency": 2.0,
        },
        "cost": {
            "target_throughput": 100.0,
            "per_instance_throughput": 30.0,
            "price_per_hr": 0.5,
            "instance_type": "c6i.xlarge",
            "price_source": "https://aws.amazon.com/ec2/pricing/on-demand/",
        },
    }


@pytest.fixture
def injected():
    return _canonical_injected()


@pytest.fixture
def clone_injected():
    """A9 등에서 표준 주입을 깊은 복사해 부분 변형하기 위한 팩토리."""
    def _make():
        return copy.deepcopy(_canonical_injected())

    return _make
