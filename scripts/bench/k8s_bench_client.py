"""k8s 인-클러스터 벤치 클라이언트 — 파드에서 실행돼 ClusterIP 서비스들을 때린다.

로컬 러너와 달리 **파드↔파드 실 네트워크**(kube-proxy/iptables 경유)를 지나므로 client
벽시계에 진짜 k8s 네트워크가 포함된다. 합성 결정론 스트림(vitals9/vitals_labs18)으로 각
타깃의 latency(client+server _sum 델타, R회 반복)·throughput·게이트 피처라인을 재고 JSON 을
stdout 으로 낸다(호스트가 kubectl logs 로 수거). 메모리는 호스트가 kubectl exec 로 별도 수집.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
import re
import statistics as st
import time

import httpx

WARMUP = 15
N_LAT = 100
REPEATS = 5
TP_STREAMS = 4
TP_PER = 30

_SUM_RE = re.compile(r"^serve_predict_latency_seconds_sum\s+([0-9.eE+-]+)", re.M)
_FEAT_RE = re.compile(r"^serve_input_feature_value_count\{", re.M)

VITALS = ["HR", "O2Sat", "Temp", "SBP", "MAP", "DBP", "Resp", "Age", "Gender"]
LABS = ["WBC", "BUN", "Platelets", "Lactate", "Creatinine", "Glucose", "PTT", "HCO3", "Calcium"]


def _row(fs: str, step: int) -> dict:
    s = float(step)
    r = {"HR": 80 + 4 * s, "O2Sat": 98 - 0.6 * s, "Temp": 37 + 0.25 * s, "SBP": 120 - 3 * s,
         "MAP": 85 - 2 * s, "DBP": 70 - 1.5 * s, "Resp": 16 + 1.2 * s, "Age": 64.0, "Gender": 1.0}
    if fs == "vitals_labs":
        r.update({"WBC": 8 + 0.3 * s, "BUN": 15 + 0.5 * s, "Platelets": 200 - s, "Lactate": 1.2 + 0.1 * s,
                  "Creatinine": 0.9 + 0.02 * s, "Glucose": 120 + s, "PTT": 30 + 0.4 * s,
                  "HCO3": 24 - 0.1 * s, "Calcium": 8.5})
    return r


def _server_sum(cl, base):
    m = _SUM_RE.search(cl.get(f"{base}/metrics", timeout=5.0).text)
    return float(m.group(1)) if m else float("nan")


def _feature_lines(cl, base):
    return len(_FEAT_RE.findall(cl.get(f"{base}/metrics", timeout=5.0).text))


def _window(cl, base, pid, fs, n):
    client, server = [], []
    prev = _server_sum(cl, base)
    for i in range(n):
        feat = _row(fs, WARMUP + i)
        t0 = time.perf_counter()
        r = cl.post(f"{base}/predict", json={"patient_id": pid, "features": feat})
        client.append((time.perf_counter() - t0) * 1000.0)
        r.raise_for_status()
        cur = _server_sum(cl, base)
        server.append(max(0.0, (cur - prev)) * 1000.0)
        prev = cur
    return client, server


def _throughput(base, fs):
    def _one(k):
        with httpx.Client(timeout=20.0) as cl:
            for i in range(TP_PER):
                cl.post(f"{base}/predict", json={"patient_id": f"tp-{k}", "features": _row(fs, i)})
        return TP_PER
    t0 = time.perf_counter()
    with cf.ThreadPoolExecutor(max_workers=TP_STREAMS) as ex:
        list(ex.map(_one, range(TP_STREAMS)))
    wall = time.perf_counter() - t0
    return {"n_streams": TP_STREAMS, "patient_ids": [f"tp-{k}" for k in range(TP_STREAMS)],
            "req_per_sec": (TP_PER * TP_STREAMS) / wall if wall else 0.0, "wall_seconds": wall}


def _measure(name, base, fs):
    with httpx.Client(timeout=20.0) as cl:
        for i in range(WARMUP):
            cl.post(f"{base}/predict", json={"patient_id": "lat", "features": _row(fs, i)})
        reps = []
        client_all, server_all = [], []
        for _ in range(REPEATS):
            c, s = _window(cl, base, "lat", fs, N_LAT)
            reps.append((st.mean(c), st.mean(s)))
            client_all += c
            server_all += s
        feat_lines = _feature_lines(cl, base)
    tp = _throughput(base, fs)
    return {"reps": reps, "client_all": client_all, "server_all": server_all,
            "feature_lines": feat_lines, "throughput": tp}


def main():
    # targets: env TARGETS = "name=url=fs;..." (호스트가 주입) 또는 기본 서비스명.
    spec = os.environ.get("TARGETS", "")
    targets = []
    if spec:
        for item in spec.split(";"):
            n, u, fs = item.split("=")
            targets.append((n, u, fs))
    out = {}
    for name, url, fs in targets:
        out[name] = _measure(name, url, fs)
    print("BENCH_JSON_START")
    print(json.dumps(out))
    print("BENCH_JSON_END")


if __name__ == "__main__":
    main()
