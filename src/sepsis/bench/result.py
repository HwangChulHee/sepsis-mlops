"""serving-benchmark 2B — BenchResult 구조화 결과 객체 + 집계 로직.

TDD 대상(§A A0): 손으로 쓰는 마크다운이 아니라 **구조화 결과 객체**(`BenchResult`)가
성공기준의 계약면이다. 정직성 논증(A2 residual·tax)은 **버킷 무관 평균**(client_mean −
server_mean) 위에서만 성립하며(B-R2-1), client·server 는 **동일 정상상태 슬라이스**의
요청별 계열이라야 페어링이 성립한다(B-R3-1).

이 모듈은 **주입된 원시 측정치**(client 벽시계 배열·server latency 요청별 계열·RSS·
throughput·비용 입력)를 받아 BenchResult 를 조립한다 — 라이브 서버 구동은 별도(수집기).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# 정상상태 컷 상수(§A A5-c). driving 배열 = client 벽시계 계열(m-R4-1).
DEFAULT_K = 20          # 수렴 판정 창 크기
THRESHOLD = 0.15        # 직전 창 p95 대비 허용 변동(|Δ| <= 0.15, 경계 포함)

# residual_label / headline_label enum 고정값(§A A2·A6 — 동등성 검사, substring 아님)
LABEL_ARM1 = "client_server_residual"       # arm-1: "network" 금지
LABEL_ARM2 = "network_plus_serialization"   # arm-2: network 추정은 여기서만
HEADLINE = "combined_deployment_profile"    # "pure_architecture" 금지


@dataclass
class Quantiles:
    p50: float
    p95: float
    p99: float


@dataclass
class ArmLatency:
    client: Quantiles          # 벽시계 분위수 — 분포 리포트용(load-bearing 아님)
    server: Quantiles          # 서버 분위수(버킷보간) — 분포 리포트용
    client_mean: float         # 정상상태 슬라이스 평균 — 버킷 무관(load-bearing)
    server_mean: float         # 정상상태 슬라이스 평균(요청별 계열, B-R3-1)
    residual: float            # == client_mean − server_mean (A2-a2)
    residual_label: str        # enum (A2)


@dataclass
class MemoryBreakdown:
    rss: float                 # arm-1(배포 계측 프로파일) RSS
    peak: float | None
    instrumentation: float | None   # == rss_arm1 − rss_arm2 (값 검증되는 유일 기여, A4-b3)
    state: float | None             # presence-only (환자수 sweep 기울기, A4-b1)
    input_dim: float | None         # 동일 아키텍처 featureset delta(control9 − arm1 rss, A4-b2)


@dataclass
class Throughput:
    n_streams: int
    unique_patient_ids: int    # == n_streams (유일 pid N개, A3)
    req_per_sec: float
    wall_seconds: float


@dataclass
class ModelBench:
    arm1: ArmLatency           # 부가 계측 ON (배포 계측 프로파일)
    arm2: ArmLatency           # 부가 계측 OFF (순수 추론 프로파일)
    tax: float                 # == arm1.residual − arm2.residual (A2-c)
    boot_latency: float        # 부팅 비용(모델 로드 + GRU 캘리브레이션), 정상상태와 분리
    steady_state_start: int    # 정상상태 컷 index(driving=arm1.client), 비수렴 시 −1 = FAIL
    throughput: Throughput
    memory: MemoryBreakdown
    stateless_claim: bool      # 계약값 False (XGB 도 lookback 버퍼 상태 있음, A4-c)


@dataclass
class ControlModel:
    """통제 arm(동일 featureset) 모델의 최소 표현 — memory.rss 접근(m-R2-2)."""
    memory: MemoryBreakdown


@dataclass
class ControlArm:
    gru9: ControlModel         # GRU/vitals9 (배포==통제 동일 featureset)
    xgb9: ControlModel         # XGB/vitals9 (배포 featureset과 다름)


@dataclass
class Attribution:
    metric: str                # 어느 지표의 분해인지 (예 "memory.rss")
    featureset_contrib: float  # 값 검증 — 동일 아키텍처 9→18 delta (== −input_dim)
    arch_contrib: float | None # presence-only (NB2 state 형태차 섞임)


@dataclass
class CostResult:
    target_throughput: float
    per_instance_throughput: float
    instance_count: int        # == ceil(target / per_instance)
    price_per_hr: float
    cost_per_hr: float         # == instance_count × price_per_hr
    instance_type: str
    price_source: str


@dataclass
class BenchResult:
    gru: ModelBench
    xgb: ModelBench
    headline_label: str        # enum (A6-a)
    control_arm: ControlArm
    attribution: list          # list[Attribution] (A6-c)
    cost: CostResult


# --- 정상상태 컷 (A5-c) -----------------------------------------------------
def steady_state_cut(latency_array, k: int = DEFAULT_K, threshold: float = THRESHOLD):
    """latency 배열의 정상상태 시작 index 와 수렴 여부 → (start, ok).

    (1) index 0 은 무조건 제외(캘리브레이션/콜드 1회성). (2) index 1 부터 크기 k 창의 p95 가
    **직전 창 p95 대비 ±threshold 이내(|Δ| <= threshold, 경계 포함)** 로 든 **첫 창의 시작
    index** 를 steady_state_start 로 고정. (3) 끝까지 수렴 못 하면 (−1, False) = run FAIL —
    '적당히 자르고 진행' 경로는 없다. 같은 배열 → 같은 결과(결정론)."""
    arr = [float(x) for x in latency_array]
    n = len(arr)
    if n < 2:
        return (-1, False)
    # index 0 제외 후 **크기 k 완전 창**들의 (시작 index, p95).
    # 꼬리에서 짧아진 창은 제외 — 안 그러면 지수 증가 배열에서 창이 max 로 수렴해 거짓 수렴한다.
    windows = []
    for s in range(1, n):
        w = arr[s:s + k]
        if len(w) < k:
            break
        windows.append((s, float(np.percentile(w, 95))))
    if not windows:
        return (-1, False)
    if len(windows) == 1:
        return (windows[0][0], True)   # 단일 후보 창 → 그 지점에서 정상상태
    for i in range(1, len(windows)):
        s, cur = windows[i]
        prev = windows[i - 1][1]
        denom = abs(prev) if prev != 0 else 1e-12
        if abs(cur - prev) / denom <= threshold:
            return (s, True)
    return (-1, False)


def _quantiles(vals) -> Quantiles:
    if len(vals) == 0:
        return Quantiles(0.0, 0.0, 0.0)
    p = np.percentile(vals, [50, 95, 99])
    return Quantiles(float(p[0]), float(p[1]), float(p[2]))


def _arm(arm_inj: dict, label: str, k: int):
    """주입된 arm 측정치 → (ArmLatency, (start, ok)). client 를 driving 배열로 컷.
    client·server 는 **동일 슬라이스** 평균(B-R3-1) — server 는 요청별 계열."""
    client = [float(x) for x in arm_inj["client"]]
    server = [float(x) for x in arm_inj["server"]]
    start, ok = steady_state_cut(client, k, THRESHOLD)
    s = start if ok else 1            # 비수렴이어도 평균은 산출(steady_state_start 로 −1 보고)
    client_slice = client[s:]
    server_slice = server[s:]
    client_mean = float(np.mean(client_slice)) if client_slice else float("nan")
    server_mean = float(np.mean(server_slice)) if server_slice else float("nan")
    residual = client_mean - server_mean
    client_q = _quantiles(client_slice)
    if "server_quantiles" in arm_inj:            # 버킷보간 분위수 주입 시 그대로(A9 검증용)
        sq = arm_inj["server_quantiles"]
        server_q = Quantiles(float(sq["p50"]), float(sq["p95"]), float(sq["p99"]))
    else:
        server_q = _quantiles(server_slice)
    arm = ArmLatency(client=client_q, server=server_q, client_mean=client_mean,
                     server_mean=server_mean, residual=residual, residual_label=label)
    return arm, (start, ok)


def _model(inj: dict, control9_rss: float, k: int) -> ModelBench:
    arm1, cut1 = _arm(inj["arm1"], LABEL_ARM1, k)
    arm2, _ = _arm(inj["arm2"], LABEL_ARM2, k)
    rss1, rss2 = float(inj["arm1"]["rss"]), float(inj["arm2"]["rss"])
    mem = MemoryBreakdown(
        rss=rss1, peak=float(inj["arm1"]["peak"]),
        instrumentation=rss1 - rss2,       # 값 검증 기여(A4-b3)
        state=None,                        # presence-only(환자수 sweep 기울기, 단일 스냅샷 검증 불가)
        input_dim=float(control9_rss) - rss1,  # 동일 아키텍처 featureset delta(A4-b2)
    )
    t = inj["throughput"]
    thr = Throughput(n_streams=int(t["n_streams"]),
                     unique_patient_ids=len(set(t["patient_ids"])),
                     req_per_sec=float(t["req_per_sec"]),
                     wall_seconds=float(t["wall_seconds"]))
    start1, ok1 = cut1
    return ModelBench(arm1=arm1, arm2=arm2, tax=arm1.residual - arm2.residual,
                      boot_latency=float(inj["boot_latency"]),
                      steady_state_start=start1 if ok1 else -1,
                      throughput=thr, memory=mem, stateless_claim=False)


def assemble_bench_result(injected: dict, *, k: int = DEFAULT_K) -> BenchResult:
    """주입된 원시 측정치 dict → BenchResult(§A A0 스키마). 순수 집계(모델 버전 무관)."""
    gru9_rss = float(injected["gru"]["control9"]["rss"])
    xgb9_rss = float(injected["xgb"]["control9"]["rss"])
    gru = _model(injected["gru"], gru9_rss, k)
    xgb = _model(injected["xgb"], xgb9_rss, k)

    control_arm = ControlArm(
        gru9=ControlModel(MemoryBreakdown(rss=gru9_rss, peak=None, instrumentation=None,
                                          state=None, input_dim=None)),
        xgb9=ControlModel(MemoryBreakdown(rss=xgb9_rss, peak=None, instrumentation=None,
                                          state=None, input_dim=None)),
    )
    # 귀인(A6-c): memory.rss 의 featureset 기여 = xgb − xgb9 (== −input_dim, 동일 아키텍처).
    # arch 기여는 presence-only(동일 featureset 아키텍처차, NB2 state 형태차 섞임).
    attribution = [Attribution(
        metric="memory.rss",
        featureset_contrib=xgb.memory.rss - xgb9_rss,
        arch_contrib=gru9_rss - xgb9_rss,
    )]

    c = injected["cost"]
    instance_count = math.ceil(float(c["target_throughput"]) / float(c["per_instance_throughput"]))
    cost = CostResult(
        target_throughput=float(c["target_throughput"]),
        per_instance_throughput=float(c["per_instance_throughput"]),
        instance_count=instance_count,
        price_per_hr=float(c["price_per_hr"]),
        cost_per_hr=instance_count * float(c["price_per_hr"]),
        instance_type=str(c["instance_type"]),
        price_source=str(c["price_source"]),
    )
    return BenchResult(gru=gru, xgb=xgb, headline_label=HEADLINE,
                       control_arm=control_arm, attribution=attribution, cost=cost)
