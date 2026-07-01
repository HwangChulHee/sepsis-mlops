"""벤치 하니스 2b §A — BenchResult 계약 TDD (구현 전 RED).

기준 문서: handoff_2b §A (계약·성공기준·실패모드). src 미참조(출제자-응시자 분리).

테스트 대상: 하니스의 **집계·귀인·라벨링 로직**(주입된 원시 측정치 → BenchResult 필드).
라이브 서버·라이브 게이트는 구동하지 않는다(입력 주입 계약 M-2). 골든은 손 계산 산술이며
라이브 모델 golden이 아니다(순수 집계라 모델 버전 무관).

seam(tests/bench/conftest.py):
  - assemble        : _assemble_bench_result(injected) → BenchResult  (main이 연결)
  - steady_cut      : _steady_state_cut(arr, k, threshold) → (start, ok)  (main이 연결)
  - injected        : 표준 골든 주입 dict
  - clone_injected  : 표준 주입 깊은 복사 팩토리(부분 변형용)

주입 형태·골든 유도는 conftest.py 상단 docstring 참조.
"""

from __future__ import annotations

import math

import pytest

TOL = dict(rel=1e-9, abs=1e-9)


# ===========================================================================
# A0 — BenchResult 스키마 (필드·중첩·타입·enum presence)
# ===========================================================================
def test_a0_top_level_fields(assemble, injected):
    """설계결정 A0: BenchResult 최상위 필드 presence."""
    r = assemble(injected)
    for name in ("gru", "xgb", "headline_label", "control_arm", "attribution", "cost"):
        assert hasattr(r, name), f"BenchResult에 {name} 필드 없음"
    assert isinstance(r.headline_label, str)
    assert isinstance(r.attribution, list) and len(r.attribution) >= 1


def test_a0_modelbench_fields(assemble, injected):
    """설계결정 A0: ModelBench 필드 presence + 타입."""
    r = assemble(injected)
    for m in (r.gru, r.xgb):
        for name in (
            "arm1", "arm2", "tax", "boot_latency", "steady_state_start",
            "throughput", "memory", "stateless_claim",
        ):
            assert hasattr(m, name), f"ModelBench에 {name} 없음"
        assert isinstance(m.tax, float)
        assert isinstance(m.steady_state_start, int)
        assert isinstance(m.stateless_claim, bool)


def test_a0_armlatency_fields(assemble, injected):
    """설계결정 A0: ArmLatency 필드 presence."""
    r = assemble(injected)
    for arm in (r.gru.arm1, r.gru.arm2, r.xgb.arm1, r.xgb.arm2):
        for name in (
            "client", "server", "client_mean", "server_mean",
            "residual", "residual_label",
        ):
            assert hasattr(arm, name), f"ArmLatency에 {name} 없음"
        assert isinstance(arm.residual_label, str)


def test_a0_quantiles_fields(assemble, injected):
    """설계결정 A0: Quantiles(p50/p95/p99) 필드 presence."""
    r = assemble(injected)
    for q in (r.gru.arm1.client, r.gru.arm1.server):
        for name in ("p50", "p95", "p99"):
            assert hasattr(q, name), f"Quantiles에 {name} 없음"


def test_a0_memory_and_cost_fields(assemble, injected):
    """설계결정 A0: MemoryBreakdown·CostResult 필드 presence."""
    r = assemble(injected)
    for name in ("rss", "peak", "instrumentation", "state", "input_dim"):
        assert hasattr(r.gru.memory, name), f"MemoryBreakdown에 {name} 없음"
    for name in (
        "target_throughput", "per_instance_throughput", "instance_count",
        "price_per_hr", "cost_per_hr", "instance_type", "price_source",
    ):
        assert hasattr(r.cost, name), f"CostResult에 {name} 없음"


# ===========================================================================
# A1 — latency 두 계측점 병행
# ===========================================================================
def test_a1a_means_filled(assemble, injected):
    """설계결정 A1-a: client_mean·server_mean 둘 다 채워짐(버킷 무관), 정상상태 슬라이스 평균."""
    r = assemble(injected)
    # 상수 구간(index 1..) 평균 == 주입 상수. 컷 index 무관.
    assert r.gru.arm1.client_mean == pytest.approx(12.0, **TOL)
    assert r.gru.arm1.server_mean == pytest.approx(5.0, **TOL)
    assert r.xgb.arm1.client_mean == pytest.approx(20.0, **TOL)
    assert r.xgb.arm1.server_mean == pytest.approx(8.0, **TOL)
    # 어느 하나라도 None이면 FAIL.
    for arm in (r.gru.arm1, r.gru.arm2, r.xgb.arm1, r.xgb.arm2):
        assert arm.client_mean is not None
        assert arm.server_mean is not None


def test_a1b_client_ge_server_quantile_sanity(assemble, injected):
    """설계결정 A1-b: client 분위수 >= server 분위수(EPS 허용) — 분포 sanity.

    주입 client(=12/20) > server(=5/8)이므로 EPS>=0 하에 sanity 성립.
    """
    r = assemble(injected)
    for arm in (r.gru.arm1, r.xgb.arm1):
        assert arm.client.p50 >= arm.server.p50
        assert arm.client.p95 >= arm.server.p95
        assert arm.client.p99 >= arm.server.p99


# ===========================================================================
# A2 — 잔차 정직성 (load-bearing, enum 동등성)
# ===========================================================================
def test_a2a_arm1_label_is_client_server_residual_not_network(assemble, injected):
    """설계결정 A2-a: arm-1 residual_label == "client_server_residual" 그리고 != "network"."""
    r = assemble(injected)
    for m in (r.gru, r.xgb):
        assert m.arm1.residual_label == "client_server_residual"
        assert m.arm1.residual_label != "network"  # 금지값(단독)


def test_a2b_arm2_label_is_network_plus_serialization(assemble, injected):
    """설계결정 A2-b: arm-2 residual_label == "network_plus_serialization"."""
    r = assemble(injected)
    for m in (r.gru, r.xgb):
        assert m.arm2.residual_label == "network_plus_serialization"


def test_a2a2_residual_definition_invariant(assemble, injected):
    """설계결정 A2-a2: residual == client_mean − server_mean (각 arm, 부동소수 허용)."""
    r = assemble(injected)
    for arm in (r.gru.arm1, r.gru.arm2, r.xgb.arm1, r.xgb.arm2):
        assert arm.residual == pytest.approx(arm.client_mean - arm.server_mean, **TOL)
    # 골든 값
    assert r.gru.arm1.residual == pytest.approx(7.0, **TOL)
    assert r.gru.arm2.residual == pytest.approx(5.0, **TOL)
    assert r.xgb.arm1.residual == pytest.approx(12.0, **TOL)
    assert r.xgb.arm2.residual == pytest.approx(9.0, **TOL)


def test_a2c_tax_is_residual_diff_invariant(assemble, injected):
    """설계결정 A2-c: tax == arm1.residual − arm2.residual (부가 계측 세금, 별도 존재)."""
    r = assemble(injected)
    for m in (r.gru, r.xgb):
        assert m.tax == pytest.approx(m.arm1.residual - m.arm2.residual, **TOL)
    assert r.gru.tax == pytest.approx(2.0, **TOL)
    assert r.xgb.tax == pytest.approx(3.0, **TOL)


# ===========================================================================
# A3 — throughput 동시 부하
# ===========================================================================
def test_a3_unique_patient_ids_equals_n_streams(assemble, injected):
    """설계결정 A3: throughput.unique_patient_ids == n_streams (유일 pid N개)."""
    r = assemble(injected)
    for m in (r.gru, r.xgb):
        assert m.throughput.unique_patient_ids == m.throughput.n_streams
        assert m.throughput.n_streams == 8
        assert m.throughput.req_per_sec is not None


# ===========================================================================
# A4 — 메모리 RSS/peak + 3기여 분해
# ===========================================================================
def test_a4a_rss_peak_present(assemble, injected):
    """설계결정 A4-a: memory.rss·memory.peak 채워짐(None이면 FAIL)."""
    r = assemble(injected)
    for m in (r.gru, r.xgb):
        assert m.memory.rss is not None
        assert m.memory.peak is not None


def test_a4b_instrumentation_value(assemble, injected):
    """설계결정 A4-b(3): instrumentation == rss_arm1 − rss_arm2 (값 검증되는 유일 기여)."""
    r = assemble(injected)
    assert r.gru.memory.instrumentation == pytest.approx(200.0 - 150.0, **TOL)  # 50
    assert r.xgb.memory.instrumentation == pytest.approx(320.0 - 300.0, **TOL)  # 20


def test_a4b_state_input_dim_presence(assemble, injected):
    """설계결정 A4-b(1,2): state·input_dim 필드 presence(값은 presence-only 축).

    세 필드(state·input_dim·instrumentation)가 다 존재하지 않으면 FAIL.
    """
    r = assemble(injected)
    _MISSING = object()
    for m in (r.gru, r.xgb):
        assert getattr(m.memory, "state", _MISSING) is not _MISSING
        assert getattr(m.memory, "input_dim", _MISSING) is not _MISSING


def test_a4b_input_dim_same_architecture_formula(assemble, injected):
    """설계결정 A4-b(2)/m-R3-1: input_dim은 동일 아키텍처 featureset delta로만.

    memory.input_dim := control_arm.xgb9.rss − xgb.rss (교차-아키텍처 금지).
    featureset_contrib(=xgb−xgb9) == −input_dim (같은 델타 반대 부호).
    """
    r = assemble(injected)
    expected_input_dim = r.control_arm.xgb9.memory.rss - r.xgb.memory.rss
    assert r.xgb.memory.input_dim == pytest.approx(expected_input_dim, **TOL)


def test_a4c_stateless_claim_is_false(assemble, injected):
    """설계결정 A4-c: stateless_claim == False (bool 계약, grep 아님). 양 모델."""
    r = assemble(injected)
    assert r.gru.stateless_claim is False
    assert r.xgb.stateless_claim is False


# ===========================================================================
# A5-c — 정상상태 컷(결정론) + 부팅 분리
# ===========================================================================
def test_a5c_cut_deterministic(steady_cut, const_series):
    """설계결정 A5-c(2): 같은 latency 배열 → 같은 steady_state_start(결정론)."""
    arr = const_series(10.0)
    a = steady_cut(list(arr), 1, 0.15)
    b = steady_cut(list(arr), 1, 0.15)
    assert a == b


def test_a5c_index0_always_excluded(steady_cut):
    """설계결정 A5-c(1): index 0 무조건 제외 → steady_state_start >= 1."""
    # 완전 상수(index0 값도 동일)라도 컷은 index 0을 고르지 않는다.
    arr = [10.0] * 30
    start, ok = steady_cut(arr, 1, 0.15)
    assert ok is True
    assert start >= 1


def test_a5c_convergent_returns_ok(steady_cut, const_series):
    """설계결정 A5-c(2): 수렴 배열 → ok True, start != −1, >= 1."""
    arr = const_series(10.0)  # index0 스파이크 후 상수 → 수렴
    start, ok = steady_cut(arr, 1, 0.15)
    assert ok is True
    assert start >= 1 and start != -1


def test_a5c_nonconvergent_returns_minus1_fail(steady_cut):
    """설계결정 A5-c(3): 비수렴 배열 → steady_state_start == −1 & ok False(run FAIL)."""
    # 매 스텝 60% 증가 → 어떤 창도 직전 창 ±15% 이내에 들지 못함 → 비수렴.
    arr = [100.0 * (1.6 ** i) for i in range(15)]
    start, ok = steady_cut(arr, 1, 0.15)
    assert start == -1
    assert ok is False


def test_a5c_boundary_inclusive_le_15pct(steady_cut):
    """설계결정 A5-c(2): 경계 포함(|Δ| <= 0.15) — 정확히 15% 스텝은 수렴으로 센다.

    tail이 전부 '정확히 15%' 스텝(200→230→264.5)이라, `<=`면 수렴(ok True),
    strict `<`였다면 이 배열은 −1이 된다 → 경계 inclusivity를 판별.
    """
    arr = [999.0, 100.0, 200.0, 230.0, 264.5]  # 30/200 == 0.15, 34.5/230 == 0.15
    start, ok = steady_cut(arr, 1, 0.15)
    assert ok is True
    assert start != -1


def test_a5c_assembly_nonconvergent_steady_start_minus1(assemble, clone_injected):
    """설계결정 A5-c(3): 조립 단계 — 비수렴 client 배열 → ModelBench.steady_state_start == −1.

    비수렴은 '적당히 자르고 진행'이 아니라 −1(run FAIL) 신호로 고정(집계 산출 금지).
    driving 배열은 client 벽시계 계열(m-R4-1).
    """
    inj = clone_injected()
    inj["gru"]["arm1"]["client"] = [100.0 * (1.6 ** i) for i in range(60)]
    r = assemble(inj)
    assert r.gru.steady_state_start == -1


def test_a5c_steady_state_start_present_and_convergent(assemble, injected):
    """설계결정 A5-c/m-R2-1: steady_state_start presence, 수렴 시 >= 1 (index0 제외)."""
    r = assemble(injected)
    for m in (r.gru, r.xgb):
        assert isinstance(m.steady_state_start, int)
        assert m.steady_state_start >= 1
        assert m.steady_state_start != -1


def test_a5c_boot_latency_present(assemble, injected):
    """설계결정 A5-c/m-R2-1: boot_latency 별도 항목으로 두 모델 대칭 기재."""
    r = assemble(injected)
    assert r.gru.boot_latency == pytest.approx(1.5, **TOL)
    assert r.xgb.boot_latency == pytest.approx(2.0, **TOL)


# ===========================================================================
# A6 — 결합 배포 프로파일 + 통제 arm + 귀인
# ===========================================================================
def test_a6a_headline_label_combined_not_pure(assemble, injected):
    """설계결정 A6-a: headline_label == "combined_deployment_profile" 그리고 != "pure_architecture"."""
    r = assemble(injected)
    assert r.headline_label == "combined_deployment_profile"
    assert r.headline_label != "pure_architecture"  # 금지값


def test_a6b_control_arm_present(assemble, injected):
    """설계결정 A6-b: control_arm.gru9·.xgb9 둘 다 존재(통제 arm 없으면 FAIL)."""
    r = assemble(injected)
    assert r.control_arm is not None
    assert r.control_arm.gru9 is not None
    assert r.control_arm.xgb9 is not None
    # 접근 경로 .memory.rss (m-R2-2) — 값 검증(주입 통제 RSS)
    assert r.control_arm.xgb9.memory.rss == pytest.approx(250.0, **TOL)


def test_a6c_attribution_nonempty_with_fields(assemble, injected):
    """설계결정 A6-c: attribution 비어있지 않고 각 항목이 두 기여 + metric 라벨을 가짐."""
    r = assemble(injected)
    assert isinstance(r.attribution, list) and len(r.attribution) >= 1
    _MISSING = object()
    for a in r.attribution:
        assert isinstance(a.metric, str) and a.metric != ""
        assert isinstance(a.featureset_contrib, float)
        assert getattr(a, "arch_contrib", _MISSING) is not _MISSING  # presence-only


def test_a6c_featureset_contrib_invariant(assemble, injected):
    """설계결정 A6-c: featureset_contrib(memory.rss) == xgb.rss − xgb9.rss (동일 아키텍처 9→18 delta).

    부호 관계(m-R3-1): featureset_contrib == −memory.input_dim.
    """
    r = assemble(injected)
    attr = next((a for a in r.attribution if a.metric == "memory.rss"), None)
    assert attr is not None, "attribution에 metric='memory.rss' 항목 없음"
    expected = r.xgb.memory.rss - r.control_arm.xgb9.memory.rss
    assert attr.featureset_contrib == pytest.approx(expected, **TOL)
    assert attr.featureset_contrib == pytest.approx(-r.xgb.memory.input_dim, **TOL)


# ===========================================================================
# A7 — 비용 구조화 산출
# ===========================================================================
def test_a7_instance_count_ceil(assemble, injected):
    """설계결정 A7: instance_count == ceil(target / per_instance)."""
    r = assemble(injected)
    expected = math.ceil(r.cost.target_throughput / r.cost.per_instance_throughput)
    assert r.cost.instance_count == expected
    assert r.cost.instance_count == 4  # ceil(100/30)


def test_a7_cost_per_hr(assemble, injected):
    """설계결정 A7: cost_per_hr == instance_count × price_per_hr."""
    r = assemble(injected)
    assert r.cost.cost_per_hr == pytest.approx(
        r.cost.instance_count * r.cost.price_per_hr, **TOL
    )
    assert r.cost.cost_per_hr == pytest.approx(2.0, **TOL)  # 4 * 0.5


def test_a7_source_fields_nonempty(assemble, injected):
    """설계결정 A7: instance_type·price_source 비어있지 않음(출처·재현성)."""
    r = assemble(injected)
    assert isinstance(r.cost.instance_type, str) and r.cost.instance_type != ""
    assert isinstance(r.cost.price_source, str) and r.cost.price_source != ""


# ===========================================================================
# A9 — load-bearing 지표는 버킷 무관
# ===========================================================================
def test_a9_residual_bucket_independent(assemble, clone_injected):
    """설계결정 A9: residual·tax는 버킷 무관 — 서로 다른 버킷 해상도(server 분위수)에도 동일.

    server 요청별 계열은 동일하게 두고 server_quantiles(버킷보간 분위수)만 다르게 주입 →
    residual(=client_mean−server_mean, 계열 기반)·tax 불변. (분위수는 분포 리포트용, B-R2-1.)
    """
    inj_a = clone_injected()
    inj_b = clone_injected()
    # 동일 server 계열, 다른 버킷 해상도 분위수만 주입.
    inj_a["gru"]["arm1"]["server_quantiles"] = {"p50": 5.0, "p95": 5.0, "p99": 5.0}
    inj_b["gru"]["arm1"]["server_quantiles"] = {"p50": 6.0, "p95": 7.0, "p99": 8.0}

    ra = assemble(inj_a)
    rb = assemble(inj_b)

    assert ra.gru.arm1.residual == pytest.approx(rb.gru.arm1.residual, **TOL)
    assert ra.gru.tax == pytest.approx(rb.gru.tax, **TOL)
    # 두 케이스 모두 계열 기반 골든과 일치.
    assert ra.gru.arm1.residual == pytest.approx(7.0, **TOL)
