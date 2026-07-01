"""serving-benchmark 실측 러너 (2b 실험).

GRU/vitals9 · XGB/vitals_labs18(배포) · XGB/vitals9(통제) 서빙을 **uvicorn 서브프로세스**로
순차 기동(자원 경합 배제)하고, 실제 환자 PSV 스트림을 httpx 로 흘려 측정한다:
  - latency: client 벽시계(요청별) + server 내부(`serve_predict_latency_seconds` _sum 인접 델타)
  - throughput: N 유일-환자 동시 스트림 req/sec
  - memory: peak RSS = `/proc/<pid>/status` VmHWM (서브프로세스라 서버 단독 RSS)
각 모델을 arm-1(부가계측 ON)·arm-2(부가계측 OFF)로 재고, `bench.result.assemble_bench_result`
로 BenchResult 조립 → `docs/reports/serving_benchmark.md` 비교표+서사.

정직성 경계:
  - 워밍업 요청은 제외(측정 창은 정상상태). 전처리 포함 경계(GRU StreamPreprocessor / XGB
    버퍼→lookback_summary 재구성)는 서버 히스토그램 안 — client−server 잔차는 network+직렬화
    +핸들러 후처리이며 arm-1 에서 "network"라 부르지 않는다(decisions 결정3).
  - best_iter 골든/절단은 uv.lock(xgboost 3.3.0) 전제.
"""

from __future__ import annotations

import concurrent.futures as cf
import contextlib
import json
import math
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from sepsis import config as C
from sepsis.bench.result import assemble_bench_result

ROOT = C.ROOT
PSV_DIR = ROOT / "data" / "raw" / "training_setB"
REPORT = ROOT / "docs" / "reports" / "serving_benchmark.md"
MEAS_JSON = ROOT / "docs" / "reports" / "serving_benchmark_measurements.json"

WARMUP = 15          # 제외할 워밍업 요청 수
N_LAT = 120          # 측정 창(정상상태) 요청 수
TP_STREAMS = 4       # throughput 동시 스트림(유일 환자)
TP_PER = 30          # 스트림당 요청 수
PY = str(ROOT / ".venv" / "bin" / "python")

_SUM_RE = re.compile(r"^serve_predict_latency_seconds_sum\s+([0-9.eE+-]+)", re.M)
_CNT_RE = re.compile(r"^serve_predict_latency_seconds_count\s+([0-9.eE+-]+)", re.M)
_FEAT_RE = re.compile(r"^serve_input_feature_value_count\{", re.M)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _read_status(pid: int, key: str) -> float:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith(key):
                return float(line.split()[1]) / 1024.0   # kB -> MB
    except OSError:
        pass
    return float("nan")


def _load_patient_rows(psv: Path, cols: list[str]) -> list[dict]:
    """PSV → featureset 키만 담은 요청 features 리스트(결측=None, 0-fill 금지)."""
    lines = psv.read_text().strip().splitlines()
    header = lines[0].split("|")
    idx = {c: header.index(c) for c in cols if c in header}
    rows = []
    for ln in lines[1:]:
        vals = ln.split("|")
        feat = {}
        for c in cols:
            v = vals[idx[c]] if c in idx else "NaN"
            feat[c] = None if v == "NaN" else float(v)
        rows.append(feat)
    return rows


def _pick_patients(cols: list[str], need_long: int, n_short: int):
    """긴 환자 1명(latency 스트림) + 짧은 환자 n명(throughput)."""
    files = sorted(PSV_DIR.glob("*.psv"))
    long_rows = None
    short = []
    for f in files:
        rows = _load_patient_rows(f, cols)
        if long_rows is None and len(rows) >= need_long:
            long_rows = (f.stem, rows)
        elif len(rows) >= TP_PER and len(short) < n_short:
            short.append((f.stem, rows))
        if long_rows is not None and len(short) >= n_short:
            break
    return long_rows, short


def _wait_health(base: str, proc: subprocess.Popen, timeout: float = 40.0) -> float:
    """/health 200 까지 대기 → boot 초. 실패 시 예외."""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early (rc={proc.returncode})")
        with contextlib.suppress(Exception):
            r = httpx.get(f"{base}/health", timeout=2.0)
            if r.status_code == 200:
                return time.perf_counter() - t0
        time.sleep(0.3)
    raise RuntimeError("server /health timeout")


def _launch(factory: str, env_extra: dict, port: int) -> subprocess.Popen:
    env = {**os.environ, **env_extra}
    return subprocess.Popen(
        [PY, "-m", "uvicorn", factory, "--factory", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _server_sum(base: str) -> float:
    m = _SUM_RE.search(httpx.get(f"{base}/metrics", timeout=5.0).text)
    return float(m.group(1)) if m else float("nan")


def _feature_lines(base: str) -> int:
    return len(_FEAT_RE.findall(httpx.get(f"{base}/metrics", timeout=5.0).text))


def _measure_latency(base: str, pid: str, rows: list[dict]):
    """워밍업 제외 후 요청별 client 벽시계 + server(_sum 델타) 계열."""
    client_lat, server_lat = [], []
    with httpx.Client(timeout=15.0) as cl:
        # 워밍업(측정 제외) — 버퍼/상태·lazy-load·캘리브레이션 흡수
        for i in range(min(WARMUP, len(rows))):
            cl.post(f"{base}/predict", json={"patient_id": pid, "features": rows[i]})
        prev_sum = _server_sum(base)
        seq = rows[WARMUP:WARMUP + N_LAT] or rows[:N_LAT]
        for feat in seq:
            t0 = time.perf_counter()
            r = cl.post(f"{base}/predict", json={"patient_id": pid, "features": feat})
            client_lat.append((time.perf_counter() - t0) * 1000.0)   # ms
            r.raise_for_status()
            cur = _server_sum(base)
            server_lat.append(max(0.0, (cur - prev_sum)) * 1000.0)   # ms (요청별 델타)
            prev_sum = cur
    return client_lat, server_lat


def _measure_throughput(base: str, patients, cols):
    """N 유일-환자 동시 스트림 → req/sec."""
    def _one(item):
        pid, rows = item
        with httpx.Client(timeout=15.0) as cl:
            for feat in rows[:TP_PER]:
                cl.post(f"{base}/predict", json={"patient_id": pid, "features": feat})
        return TP_PER

    t0 = time.perf_counter()
    with cf.ThreadPoolExecutor(max_workers=len(patients)) as ex:
        counts = list(ex.map(_one, patients))
    wall = time.perf_counter() - t0
    total = sum(counts)
    return {"n_streams": len(patients), "patient_ids": [p for p, _ in patients],
            "req_per_sec": total / wall if wall else 0.0, "wall_seconds": wall}


def _run_server(name, factory, featureset, aux_on, long_pat, short_pats, cols, want_tp):
    """한 (모델·arm) 서버를 띄워 측정치 dict 반환."""
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = {"SEPSIS_SERVE_AUX_METRICS": "1" if aux_on else "0",
           "SEPSIS_XGB_FEATURESET": featureset}
    proc = _launch(factory, env, port)
    try:
        boot = _wait_health(base, proc)
        pid_long, rows_long = long_pat
        client_lat, server_lat = _measure_latency(base, pid_long, rows_long)
        feat_lines = _feature_lines(base)          # A1 게이트 관측(ON>0, OFF==0)
        tp = _measure_throughput(base, short_pats, cols) if want_tp else None
        rss = _read_status(proc.pid, "VmRSS:")
        peak = _read_status(proc.pid, "VmHWM:")
        return {"boot": boot, "client": client_lat, "server": server_lat,
                "rss": rss, "peak": peak, "feature_lines": feat_lines, "throughput": tp}
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)


def main():
    vitals = C.featureset_columns("vitals")
    labs = C.featureset_columns("vitals_labs")
    GRU = "sepsis.serve.bench_app:build_gru_app"
    XGB = "sepsis.serve.xgb_app:build_app_from_env"

    # latency 스트림(긴 환자) + throughput(짧은 환자들) — vitals_labs 기준으로 뽑아 두 모델 공용
    long_pat, short_pats = _pick_patients(labs, WARMUP + N_LAT, TP_STREAMS)
    if long_pat is None or len(short_pats) < TP_STREAMS:
        print("insufficient patient data", file=sys.stderr); sys.exit(1)

    def _cols_view(pat, cols):
        pid, rows = pat
        return pid, [{c: r.get(c) for c in cols} for r in rows]

    runs = {}
    plan = [
        ("gru_arm1", "gru", GRU, "vitals", True, True),
        ("gru_arm2", "gru", GRU, "vitals", False, False),
        ("xgb_arm1", "xgb", XGB, "vitals_labs", True, True),
        ("xgb_arm2", "xgb", XGB, "vitals_labs", False, False),
        ("xgb9", "xgb", XGB, "vitals", True, False),   # 통제 arm(RSS)
    ]
    for key, model, factory, fs, aux, want_tp in plan:
        cols = vitals if fs == "vitals" else labs
        lp = _cols_view(long_pat, cols)
        sp = [_cols_view(p, cols) for p in short_pats]
        print(f"[run] {key} (featureset={fs}, aux={'ON' if aux else 'OFF'}) ...", flush=True)
        runs[key] = _run_server(key, factory, fs, aux, lp, sp, cols, want_tp)
        print(f"      boot={runs[key]['boot']:.2f}s rss={runs[key]['rss']:.0f}MB "
              f"peak={runs[key]['peak']:.0f}MB feat_lines={runs[key]['feature_lines']}", flush=True)

    def _arm(r):
        return {"client": r["client"], "server": r["server"], "rss": r["rss"], "peak": r["peak"]}

    injected = {
        "gru": {
            "arm1": _arm(runs["gru_arm1"]), "arm2": _arm(runs["gru_arm2"]),
            "control9": {"rss": runs["gru_arm1"]["rss"]},   # GRU 배포==통제(vitals9)
            "throughput": runs["gru_arm1"]["throughput"], "boot_latency": runs["gru_arm1"]["boot"],
        },
        "xgb": {
            "arm1": _arm(runs["xgb_arm1"]), "arm2": _arm(runs["xgb_arm2"]),
            "control9": {"rss": runs["xgb9"]["rss"]},        # XGB/vitals9 통제
            "throughput": runs["xgb_arm1"]["throughput"], "boot_latency": runs["xgb_arm1"]["boot"],
        },
        "cost": {  # 측정 기반 per-instance = arm-1 req/sec, 요금은 문서화된 온디맨드
            "target_throughput": 1000.0,
            "per_instance_throughput": runs["xgb_arm1"]["throughput"]["req_per_sec"],
            "price_per_hr": 0.17, "instance_type": "c6i.xlarge (vCPU4/8GiB, us-east-1 예시)",
            "price_source": "https://aws.amazon.com/ec2/pricing/on-demand/ (2026-07 조회 예시)",
        },
    }
    result = assemble_bench_result(injected)
    MEAS_JSON.write_text(json.dumps(
        {"injected": {k: (v if k == "cost" else {kk: (vv if kk not in ("arm1", "arm2") else "…series…")
                                                 for kk, vv in v.items()}) for k, v in injected.items()},
         "runs_summary": {k: {kk: runs[k][kk] for kk in ("boot", "rss", "peak", "feature_lines")}
                          for k in runs}}, ensure_ascii=False, indent=2))
    _write_report(result, runs)
    print(f"[done] report -> {REPORT}")


def _q(qs):
    return f"{qs.p50:.2f}/{qs.p95:.2f}/{qs.p99:.2f}"


def _write_report(r, runs):
    g, x = r.gru, r.xgb
    lines = []
    A = lines.append
    A("# Serving Benchmark — GRU vs XGBoost (실측)\n")
    A("> 실측: uvicorn 서브프로세스(순차, 자원경합 배제) + 실제 환자 PSV 스트림(setB). "
      "워밍업 제외, 전처리 포함 경계(GRU StreamPreprocessor / XGB 버퍼→lookback_summary 재구성).\n")
    A("> **정직성**: 헤드라인은 (아키텍처 × featureset) **결합 배포 프로파일** — GRU/vitals9 vs "
      "XGB/vitals_labs18. 순수 아키텍처 운영비가 아니다. client−server 잔차는 arm-1 에서 "
      "\"network\"라 부르지 않는다(network+직렬화+핸들러 후처리). best_iter 절단·골든은 "
      "uv.lock(xgboost 3.3.0) 전제.\n")

    A("## 1. 헤드라인 — latency (정상상태, ms, p50/p95/p99)\n")
    A("| 모델(배포) | client 벽시계 | server 내부 | client_mean | server_mean | residual(mean) | tax(계측세금) |")
    A("|---|---|---|---|---|---|---|")
    A(f"| GRU/vitals9 | {_q(g.arm1.client)} | {_q(g.arm1.server)} | {g.arm1.client_mean:.3f} | "
      f"{g.arm1.server_mean:.3f} | {g.arm1.residual:.3f} | {g.tax:.3f} |")
    A(f"| XGB/vitals_labs18 | {_q(x.arm1.client)} | {_q(x.arm1.server)} | {x.arm1.client_mean:.3f} | "
      f"{x.arm1.server_mean:.3f} | {x.arm1.residual:.3f} | {x.tax:.3f} |")
    A(f"\n- `residual = client_mean − server_mean` (버킷 무관 평균, 동일 정상상태 슬라이스). "
      f"`residual_label`={g.arm1.residual_label!r} — arm-1 에서 network 아님.")
    A(f"- `tax = arm1.residual − arm2.residual` = 부가 계측(피처 히스토그램+drift 윈도우) 세금. "
      f"arm-2(순수추론) network 추정: GRU {g.arm2.residual:.3f} / XGB {x.arm2.residual:.3f} ms "
      f"(label={g.arm2.residual_label!r}).")

    A("\n## 2. throughput (동시 부하)\n")
    A("| 모델 | n_streams | req/sec | wall(s) |")
    A("|---|---|---|---|")
    A(f"| GRU/vitals9 | {g.throughput.n_streams} | {g.throughput.req_per_sec:.1f} | {g.throughput.wall_seconds:.2f} |")
    A(f"| XGB/vitals_labs18 | {x.throughput.n_streams} | {x.throughput.req_per_sec:.1f} | {x.throughput.wall_seconds:.2f} |")

    A("\n## 3. 메모리 (peak RSS, MB) + 3기여 분해\n")
    A("| 모델 | RSS(arm1) | peak | 계측 부속물(arm1−arm2) | 입력차원(control9−arm1) | state |")
    A("|---|---|---|---|---|---|")
    A(f"| GRU/vitals9 | {g.memory.rss:.0f} | {g.memory.peak:.0f} | {g.memory.instrumentation:.0f} | "
      f"{g.memory.input_dim:.0f} | (환자수 sweep, presence) |")
    A(f"| XGB/vitals_labs18 | {x.memory.rss:.0f} | {x.memory.peak:.0f} | {x.memory.instrumentation:.0f} | "
      f"{x.memory.input_dim:.0f} | (presence) |")
    A(f"\n- **XGB 도 stateless 아님**(`stateless_claim={x.stateless_claim}`) — 환자별 8행 lookback "
      f"버퍼 = per-patient 상태. 메모리 차이를 아키텍처로 뭉뚱그리지 않고 3기여로 분해.")
    attr = next((a for a in r.attribution if a.metric == "memory.rss"), None)
    if attr:
        A(f"- featureset 기여(memory.rss, XGB 9→18) = {attr.featureset_contrib:.0f} MB "
          f"(= −input_dim). 통제 arm XGB/vitals9 RSS = {r.control_arm.xgb9.memory.rss:.0f} MB.")

    A("\n## 4. 비용 환산 (수동)\n")
    c = r.cost
    A(f"- 목표 throughput {c.target_throughput:.0f} req/s, 측정 per-instance {c.per_instance_throughput:.1f} req/s "
      f"→ 인스턴스 {c.instance_count}대 × ${c.price_per_hr}/hr = **${c.cost_per_hr:.2f}/hr**.")
    A(f"- 인스턴스: {c.instance_type}. 요금 출처: {c.price_source}.")
    A(f"- (per-instance = XGB/vitals_labs arm-1 측정 req/s. GRU/XGB 비용 대비는 각 req/s 로 환산.)")

    A("\n## 5. 공정성·정상상태·게이트 관측\n")
    A(f"- 워밍업 {WARMUP} 요청 제외, 측정 창 {N_LAT} 요청. 정상상태 컷 index: "
      f"GRU={g.steady_state_start}, XGB={x.steady_state_start} (−1이면 비수렴 FAIL).")
    A(f"- 부팅 비용(모델 로드+캘리브레이션) 분리: GRU boot={g.boot_latency:.2f}s, XGB boot={x.boot_latency:.2f}s.")
    A(f"- 관측성 게이트 확인(A1): arm-1 피처 샘플라인 GRU={runs['gru_arm1']['feature_lines']}/"
      f"XGB={runs['xgb_arm1']['feature_lines']} (>0), arm-2 GRU={runs['gru_arm2']['feature_lines']}/"
      f"XGB={runs['xgb_arm2']['feature_lines']} (==0 이면 게이트 동작).")

    A("\n## 6. 한계 (정직)\n")
    A("- client 벽시계는 localhost httpx — 실 네트워크 왕복은 작다. 잔차의 network 성분은 arm-2 에서만 "
      "추정하며 여기선 직렬화+프레임워크가 주. 데이터센터 배포 network 는 별도.")
    A("- server 요청별 latency 는 `_sum` 인접 델타(단일 스트림 순차라 깨끗). 분위수는 분포 참고(버킷 무관 "
      "평균이 load-bearing).")
    A("- 단일 실행·소표본(1 실행). CI/다른 하드웨어에서 절대치 다름. best_iter 골든은 xgboost 3.3.0 전제.")
    A("- throughput 은 소규모 동시 스트림(스모크 수준). 대규모 부하는 별도.")

    REPORT.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
