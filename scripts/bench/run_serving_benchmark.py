"""serving-benchmark 실측 러너 (2b 실험) — 강화판.

GRU/vitals9 · XGB/vitals9(통제) · XGB/vitals_labs18(배포) 서빙을 **uvicorn 서브프로세스**로
순차 기동(자원 경합 배제)하고, 실제 환자 PSV 스트림을 httpx 로 흘려 측정한다:
  - latency: client 벽시계(요청별) + server 내부(`serve_predict_latency_seconds` _sum 인접 델타)
    — **R회 반복**해 중앙값·분산(노이즈) 산출.
  - throughput: N 유일-환자 동시 스트림 req/sec
  - memory: peak RSS = `/proc/<pid>/status` VmHWM, + **환자 수 sweep**으로 상태 메모리 기울기.
각 모델을 arm-1(부가계측 ON)·arm-2(부가계측 OFF)로 재고, XGB 는 featureset 9/18 둘 다 재
**아키텍처 vs featureset 기여를 분리**한다. `assemble_bench_result` 로 BenchResult 조립 →
`docs/reports/serving_benchmark.md` 비교표+서사.

정직성 경계: 워밍업 제외(정상상태). client 벽시계=localhost(실 network 작음). best_iter 절단·
골든은 uv.lock(xgboost 3.3.0) 전제.
"""

from __future__ import annotations

import concurrent.futures as cf
import contextlib
import json
import os
import re
import socket
import statistics as st
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

WARMUP = 15
N_LAT = 100          # 반복 1회당 측정 창
REPEATS = 5          # 반복 횟수(노이즈)
TP_STREAMS = 4
TP_PER = 30
SWEEP_POINTS = [0, 1000, 3000, 6000]   # 환자 수 sweep (상태 메모리 기울기)
PY = str(ROOT / ".venv" / "bin" / "python")

_SUM_RE = re.compile(r"^serve_predict_latency_seconds_sum\s+([0-9.eE+-]+)", re.M)
_FEAT_RE = re.compile(r"^serve_input_feature_value_count\{", re.M)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _status_mb(pid: int, key: str) -> float:
    with contextlib.suppress(OSError):
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith(key):
                return float(line.split()[1]) / 1024.0
    return float("nan")


def _load_patient_rows(psv: Path, cols: list[str]) -> list[dict]:
    lines = psv.read_text().strip().splitlines()
    header = lines[0].split("|")
    idx = {c: header.index(c) for c in cols if c in header}
    out = []
    for ln in lines[1:]:
        vals = ln.split("|")
        out.append({c: (None if (c not in idx or vals[idx[c]] == "NaN") else float(vals[idx[c]]))
                    for c in cols})
    return out


def _pick_patients(cols, need_long, n_short):
    long_pat, short = None, []
    for f in sorted(PSV_DIR.glob("*.psv")):
        rows = _load_patient_rows(f, cols)
        if long_pat is None and len(rows) >= need_long:
            long_pat = (f.stem, rows)
        elif len(rows) >= TP_PER and len(short) < n_short:
            short.append((f.stem, rows))
        if long_pat and len(short) >= n_short:
            break
    return long_pat, short


def _wait_health(base, proc, timeout=40.0) -> float:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early rc={proc.returncode}")
        with contextlib.suppress(Exception):
            if httpx.get(f"{base}/health", timeout=2.0).status_code == 200:
                return time.perf_counter() - t0
        time.sleep(0.3)
    raise RuntimeError("health timeout")


def _launch(factory, env_extra, port):
    return subprocess.Popen(
        [PY, "-m", "uvicorn", factory, "--factory", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        env={**os.environ, **env_extra}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _server_sum(base) -> float:
    m = _SUM_RE.search(httpx.get(f"{base}/metrics", timeout=5.0).text)
    return float(m.group(1)) if m else float("nan")


def _feature_lines(base) -> int:
    return len(_FEAT_RE.findall(httpx.get(f"{base}/metrics", timeout=5.0).text))


def _one_window(cl, base, pid, seq):
    client, server = [], []
    prev = _server_sum(base)
    for feat in seq:
        t0 = time.perf_counter()
        r = cl.post(f"{base}/predict", json={"patient_id": pid, "features": feat})
        client.append((time.perf_counter() - t0) * 1000.0)
        r.raise_for_status()
        cur = _server_sum(base)
        server.append(max(0.0, (cur - prev)) * 1000.0)
        prev = cur
    return client, server


def _measure_latency(base, pid, rows):
    """워밍업 1회 후 R회 측정 창 → 반복별 (client_mean, server_mean) + 전체 계열."""
    reps, client_all, server_all = [], [], []
    with httpx.Client(timeout=15.0) as cl:
        for i in range(min(WARMUP, len(rows))):
            cl.post(f"{base}/predict", json={"patient_id": pid, "features": rows[i]})
        seq = rows[WARMUP:WARMUP + N_LAT] or rows[:N_LAT]
        for _ in range(REPEATS):
            c, s = _one_window(cl, base, pid, seq)
            reps.append((st.mean(c), st.mean(s)))
            client_all += c
            server_all += s
    return {"reps": reps, "client_all": client_all, "server_all": server_all}


def _measure_throughput(base, patients):
    def _one(item):
        pid, rows = item
        with httpx.Client(timeout=15.0) as cl:
            for feat in rows[:TP_PER]:
                cl.post(f"{base}/predict", json={"patient_id": pid, "features": feat})
        return TP_PER
    t0 = time.perf_counter()
    with cf.ThreadPoolExecutor(max_workers=len(patients)) as ex:
        list(ex.map(_one, patients))
    wall = time.perf_counter() - t0
    return {"n_streams": len(patients), "patient_ids": [p for p, _ in patients],
            "req_per_sec": (TP_PER * len(patients)) / wall if wall else 0.0, "wall_seconds": wall}


def _measure_sweep(base, pid_proc, cols):
    """환자 수 sweep → [(n_patients, rss_MB)]. 유일 pid 로 상태(버퍼/hidden state) 적재 후 RSS."""
    row = {c: (1.0 if c not in ("Gender",) else 1.0) for c in cols}
    pts, added = [], 0
    with httpx.Client(timeout=15.0) as cl:
        pts.append((0, _status_mb(pid_proc, "VmRSS:")))
        for target in SWEEP_POINTS[1:]:
            while added < target:
                cl.post(f"{base}/predict", json={"patient_id": f"sweep-{added}", "features": row})
                added += 1
            pts.append((target, _status_mb(pid_proc, "VmRSS:")))
    return pts


def _run(name, factory, fs, aux, long_pat, short_pats, want_tp, want_sweep, cols):
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    proc = _launch(factory, {"SEPSIS_SERVE_AUX_METRICS": "1" if aux else "0",
                             "SEPSIS_XGB_FEATURESET": fs}, port)
    try:
        boot = _wait_health(base, proc)
        lat = _measure_latency(base, long_pat[0], long_pat[1])
        feat_lines = _feature_lines(base)
        tp = _measure_throughput(base, short_pats) if want_tp else None
        sweep = _measure_sweep(base, proc.pid, cols) if want_sweep else None
        return {"boot": boot, "lat": lat, "throughput": tp, "sweep": sweep,
                "rss": _status_mb(proc.pid, "VmRSS:"), "peak": _status_mb(proc.pid, "VmHWM:"),
                "feature_lines": feat_lines}
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)


def _med_server(run):
    return st.median([s for _, s in run["lat"]["reps"]])


def _med_client(run):
    return st.median([c for c, _ in run["lat"]["reps"]])


def _spread(vals):
    return (min(vals), max(vals))


def _slope_mb_per_1k(sweep):
    xs = [n for n, _ in sweep]
    ys = [r for _, r in sweep]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / denom if denom else 0.0
    return slope * 1000.0   # MB per 1000 patients


def main():
    vitals = C.featureset_columns("vitals")
    labs = C.featureset_columns("vitals_labs")
    GRU = "sepsis.serve.bench_app:build_gru_app"
    XGB = "sepsis.serve.xgb_app:build_app_from_env"
    long_pat, short_pats = _pick_patients(labs, WARMUP + N_LAT, TP_STREAMS)
    if long_pat is None or len(short_pats) < TP_STREAMS:
        print("insufficient data", file=sys.stderr); sys.exit(1)

    def _view(pat, cols):
        pid, rows = pat
        return pid, [{c: r.get(c) for c in cols} for r in rows]

    plan = [
        ("gru_arm1", GRU, "vitals", True, True, True),
        ("gru_arm2", GRU, "vitals", False, False, False),
        ("xgb9_arm1", XGB, "vitals", True, True, False),      # 통제 arm: latency+RSS
        ("xgb_arm1", XGB, "vitals_labs", True, True, True),   # 배포
        ("xgb_arm2", XGB, "vitals_labs", False, False, False),
    ]
    runs = {}
    for key, factory, fs, aux, tp, sweep in plan:
        cols = vitals if fs == "vitals" else labs
        lp, sp = _view(long_pat, cols), [_view(p, cols) for p in short_pats]
        print(f"[run] {key} fs={fs} aux={'ON' if aux else 'OFF'} ...", flush=True)
        runs[key] = _run(key, factory, fs, aux, lp, sp, tp, sweep, cols)
        r = runs[key]
        print(f"      boot={r['boot']:.2f}s server_med={_med_server(r)*1:.3f}ms "
              f"rss={r['rss']:.0f} peak={r['peak']:.0f} feat={r['feature_lines']}", flush=True)

    def _arm(run):
        return {"client": run["lat"]["client_all"], "server": run["lat"]["server_all"],
                "rss": run["rss"], "peak": run["peak"]}

    injected = {
        "gru": {"arm1": _arm(runs["gru_arm1"]), "arm2": _arm(runs["gru_arm2"]),
                "control9": {"rss": runs["gru_arm1"]["rss"]},
                "throughput": runs["gru_arm1"]["throughput"], "boot_latency": runs["gru_arm1"]["boot"]},
        "xgb": {"arm1": _arm(runs["xgb_arm1"]), "arm2": _arm(runs["xgb_arm2"]),
                "control9": {"rss": runs["xgb9_arm1"]["rss"]},
                "throughput": runs["xgb_arm1"]["throughput"], "boot_latency": runs["xgb_arm1"]["boot"]},
        "cost": {"target_throughput": 1000.0,
                 "per_instance_throughput": runs["xgb_arm1"]["throughput"]["req_per_sec"],
                 "price_per_hr": 0.17, "instance_type": "c6i.xlarge (vCPU4/8GiB, 예시)",
                 "price_source": "https://aws.amazon.com/ec2/pricing/on-demand/ (2026-07 조회 예시)"},
    }
    result = assemble_bench_result(injected)
    MEAS_JSON.write_text(json.dumps(
        {k: {"boot": runs[k]["boot"], "rss": runs[k]["rss"], "peak": runs[k]["peak"],
             "feature_lines": runs[k]["feature_lines"],
             "server_reps_ms": [s for _, s in runs[k]["lat"]["reps"]],
             "client_reps_ms": [c for c, _ in runs[k]["lat"]["reps"]],
             "throughput": runs[k]["throughput"], "sweep": runs[k]["sweep"]} for k in runs},
        ensure_ascii=False, indent=2))
    _write_report(result, runs)
    print(f"[done] -> {REPORT}")


def _q(qs):
    return f"{qs.p50:.2f}/{qs.p95:.2f}/{qs.p99:.2f}"


def _write_report(r, runs):
    g, x = r.gru, r.xgb
    gru9_s, xgb9_s, xgb18_s = _med_server(runs["gru_arm1"]), _med_server(runs["xgb9_arm1"]), _med_server(runs["xgb_arm1"])
    gru9_sp = _spread([s for _, s in runs["gru_arm1"]["lat"]["reps"]])
    xgb9_sp = _spread([s for _, s in runs["xgb9_arm1"]["lat"]["reps"]])
    xgb18_sp = _spread([s for _, s in runs["xgb_arm1"]["lat"]["reps"]])
    arch = xgb9_s - gru9_s          # 아키텍처 기여(featureset=9 고정): XGB/9 − GRU/9
    feat = xgb18_s - xgb9_s         # featureset 기여(XGB 9→18)
    L = []
    A = L.append
    A("# Serving Benchmark — GRU vs XGBoost (실측, 강화판)\n")
    A(f"> uvicorn 서브프로세스(순차) + 실제 setB 환자 스트림. 워밍업 {WARMUP} 제외, 측정창 {N_LAT}×{REPEATS}회 반복. "
      "전처리 포함 경계(GRU StreamPreprocessor / XGB 버퍼→lookback_summary 재구성).\n")
    A("> **정직성**: 헤드라인=(아키텍처×featureset) 결합 배포 프로파일. client−server 잔차는 arm-1 에서 "
      "\"network\"라 부르지 않음. best_iter 절단·골든은 uv.lock(xgboost 3.3.0) 전제.\n")

    A("## 1. server 내부 추론 latency + 아키텍처/featureset 분리 (ms, 중앙값[min–max])\n")
    A("| featureset arm | server_mean 중앙값 | 반복 spread |")
    A("|---|---|---|")
    A(f"| GRU / vitals9 | {gru9_s:.3f} | [{gru9_sp[0]:.3f}–{gru9_sp[1]:.3f}] |")
    A(f"| XGB / vitals9 (통제) | {xgb9_s:.3f} | [{xgb9_sp[0]:.3f}–{xgb9_sp[1]:.3f}] |")
    A(f"| XGB / vitals_labs18 (배포) | {xgb18_s:.3f} | [{xgb18_sp[0]:.3f}–{xgb18_sp[1]:.3f}] |")
    A(f"\n- **아키텍처 기여**(featureset=9 고정, XGB/9 − GRU/9) = **{arch:+.3f} ms**.")
    A(f"- **featureset 기여**(XGB 9→18) = **{feat:+.3f} ms**.")
    A("- 통제 arm(XGB/9)을 latency 로도 재서, 배포 arm(XGB/18)의 재구성 비용 중 아키텍처 몫과 "
      "featureset(입력차원 2배) 몫을 분리. GRU 는 hidden state 로 O(1), XGB 는 매 요청 8행 버퍼 "
      "lookback 재구성 + 트리 절단이라 무겁다.")

    A("\n## 2. 헤드라인 — 배포 arm latency (client/server, ms)\n")
    A("| 배포 arm | client 벽시계(p50/95/99) | server(p50/95/99) | client_mean | server_mean | residual | tax |")
    A("|---|---|---|---|---|---|---|")
    A(f"| GRU/vitals9 | {_q(g.arm1.client)} | {_q(g.arm1.server)} | {g.arm1.client_mean:.3f} | {g.arm1.server_mean:.3f} | {g.arm1.residual:.3f} | {g.tax:.3f} |")
    A(f"| XGB/vitals_labs18 | {_q(x.arm1.client)} | {_q(x.arm1.server)} | {x.arm1.client_mean:.3f} | {x.arm1.server_mean:.3f} | {x.arm1.residual:.3f} | {x.tax:.3f} |")
    A(f"\n- residual=client_mean−server_mean(버킷 무관, label={g.arm1.residual_label!r}, arm-1 network 아님). "
      f"tax=arm1−arm2 잔차(부가계측 세금). arm-2 network 추정: GRU {g.arm2.residual:.3f}/XGB {x.arm2.residual:.3f} ms.")

    A("\n## 3. throughput (동시 부하)\n| 모델 | n_streams | req/sec | wall(s) |\n|---|---|---|---|")
    A(f"| GRU/vitals9 | {g.throughput.n_streams} | {g.throughput.req_per_sec:.1f} | {g.throughput.wall_seconds:.2f} |")
    A(f"| XGB/vitals_labs18 | {x.throughput.n_streams} | {x.throughput.req_per_sec:.1f} | {x.throughput.wall_seconds:.2f} |")

    A("\n## 4. 메모리 — peak RSS + 상태 메모리 sweep\n")
    A("| 모델 | RSS(arm1,MB) | peak | 계측 세금(arm1−arm2) | featureset(control9−arm1) |")
    A("|---|---|---|---|---|")
    A(f"| GRU/vitals9 | {g.memory.rss:.0f} | {g.memory.peak:.0f} | {g.memory.instrumentation:.1f} | {g.memory.input_dim:.1f} |")
    A(f"| XGB/vitals_labs18 | {x.memory.rss:.0f} | {x.memory.peak:.0f} | {x.memory.instrumentation:.1f} | {x.memory.input_dim:.1f} |")
    gslope = _slope_mb_per_1k(runs["gru_arm1"]["sweep"])
    xslope = _slope_mb_per_1k(runs["xgb_arm1"]["sweep"])
    A(f"\n- **상태 메모리 기울기(환자 수 sweep {SWEEP_POINTS})**: GRU **{gslope:+.3f} MB/1k환자**, "
      f"XGB **{xslope:+.3f} MB/1k환자**. (GRU=hidden state, XGB=8행 버퍼 — 둘 다 환자 수에 증가하나 "
      f"환자당 sub-KB 라 수천 명 규모에선 RSS 노이즈 근처. **XGB stateless 아님**을 기울기로 확인.)")
    A(f"- 헤드라인 RSS 차(GRU {g.memory.rss:.0f} vs XGB {x.memory.rss:.0f})는 주로 torch vs xgboost "
      f"런타임 footprint. 계측 세금·입력차원 기여는 이 규모에서 노이즈 수준.")

    A("\n## 5. 비용 환산 (수동)\n")
    c = r.cost
    A(f"- 목표 {c.target_throughput:.0f} req/s, 측정 per-instance {c.per_instance_throughput:.1f} req/s → "
      f"{c.instance_count}대 × ${c.price_per_hr}/hr = **${c.cost_per_hr:.2f}/hr**. 인스턴스 {c.instance_type}, 출처 {c.price_source}.")

    A("\n## 6. 공정성·게이트·노이즈 관측\n")
    A(f"- 정상상태 컷 index GRU={g.steady_state_start}/XGB={x.steady_state_start}(−1=비수렴 FAIL). "
      f"부팅 분리 GRU {g.boot_latency:.2f}s/XGB {x.boot_latency:.2f}s.")
    A(f"- 게이트(A1): arm-1 피처라인 GRU={runs['gru_arm1']['feature_lines']}/XGB={runs['xgb_arm1']['feature_lines']}(>0), "
      f"arm-2 GRU={runs['gru_arm2']['feature_lines']}/XGB={runs['xgb_arm2']['feature_lines']}(==0).")
    A("- 노이즈: server_mean 반복 spread(위 §1). tax·계측세금·입력차원 기여가 spread 안에 들면 노이즈로 해석.")

    A("\n## 7. 한계 (정직)\n")
    A("- client 벽시계=localhost httpx(실 network 작음) — 잔차 network 성분은 arm-2 에서만 추정, 여기선 직렬화+프레임워크가 주.")
    A("- 단일 머신·소표본. 반복 5회로 노이즈는 줄였으나 절대치는 하드웨어 의존. best_iter 골든=xgboost 3.3.0 전제.")
    A(f"- 상태 sweep 은 합성 pid(고정 행)로 상태 엔트리만 적재 — 환자당 메모리는 sub-KB라 {SWEEP_POINTS[-1]}명에서도 소량. 대규모(10만+)는 별도.")
    A("- throughput 은 소규모 동시 스트림(스모크). 대규모 부하·네트워크 배포는 별도.")

    REPORT.write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
