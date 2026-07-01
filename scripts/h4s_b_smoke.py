"""H4s-b gate — FastAPI + simulator + Prometheus (h4_serving_handoff.md H4s-b).

4 programmatic asserts via FastAPI TestClient (no server needed). All PASS -> H4s-b done.

    uv run python -m scripts.h4s_b_smoke
"""

from __future__ import annotations

import ast
import sys

import numpy as np
from fastapi.testclient import TestClient

from sepsis import config as C
from sepsis.data import cache as cache_mod
from sepsis.data import split as split_mod
from sepsis.serve import simulator
from sepsis.serve.app import app
from sepsis.serve.bundle import load_bundle
from sepsis.serve.predictor import StatefulPredictor

TOL = 1e-6
FS = "vitals"


def synth_raw(T, F, seed):
    rng = np.random.default_rng(seed)
    raw = rng.normal(80.0, 20.0, size=(T, F)).astype(np.float32)
    raw[rng.random((T, F)) < 0.2] = np.nan
    raw[0:2, 0] = np.nan
    raw[:, 2] = np.nan
    return raw


def main() -> int:
    lines, ok = [], True

    def check(cond, label, detail):
        nonlocal ok
        if not cond:
            ok = False
        lines.append(f"[{'PASS' if cond else 'FAIL'}] {label}: {detail}")

    bundle = load_bundle(FS)
    cols = C.featureset_columns(FS)
    F = len(cols)

    with TestClient(app) as client:
        # --- #1 /predict·/health + missing-field -> np.nan contract ---
        h = client.get("/health")
        full_feats = {c: 70.0 + i for i, c in enumerate(cols)}
        r_full = client.post("/predict", json={"patient_id": "p_full", "features": full_feats})
        # omit "Temp": absent field must enter as np.nan (not 0/mean)
        feats_missing = {c: 70.0 + i for i, c in enumerate(cols) if c != "Temp"}
        r_api = client.post("/predict", json={"patient_id": "p_miss", "features": feats_missing})
        # direct reference: same row with Temp = np.nan, fresh predictor (first timestep)
        row = np.array([feats_missing.get(c, np.nan) if feats_missing.get(c) is not None else np.nan
                        for c in cols], dtype=np.float32)
        p_direct = StatefulPredictor(bundle).predict("ref", row)["p"]
        nan_ok = (h.status_code == 200 and r_full.status_code == 200 and r_api.status_code == 200
                  and np.isnan(row[cols.index("Temp")])
                  and abs(r_api.json()["p"] - p_direct) <= TOL)
        check(nan_ok, "#1 /predict·/health + missing->np.nan",
              f"health={h.status_code} predict={r_api.status_code} "
              f"api_p={r_api.json()['p']:.6f}==direct(nan){p_direct:.6f}; Temp entered as NaN")

        # --- #2 schema derived from run featureset (vitals 9); unknown rejected ---
        sc = client.get("/schema").json()
        r_unknown = client.post("/predict",
                                json={"patient_id": "u", "features": {"BOGUS": 1.0}})
        schema_ok = (sc["n_features"] == 9 == F and sc["features"] == cols
                     and r_unknown.status_code == 422)
        check(schema_ok, "#2 schema derived from featureset (9), unknown rejected",
              f"n_features={sc['n_features']} features=={cols == sc['features']} "
              f"unknown_field_status={r_unknown.status_code}")

        # --- #4 Prometheus /metrics exposes required metrics ---
        m = client.get("/metrics").text
        needed = ["serve_predict_requests_total", "serve_predict_latency_seconds",
                  "serve_pred_prob", "serve_alarms_total",
                  "serve_input_feature_value", "serve_input_missing_total"]
        missing_metrics = [n for n in needed if n not in m]
        check(not missing_metrics, "#4 Prometheus /metrics exposed",
              f"present={[n for n in needed if n in m]}; missing={missing_metrics or 'none'}")

    # --- #3 simulator: chronological, future-not-used (causal) + B replay observation-only ---
    rows = synth_raw(12, F, seed=7)
    full = [d["p"] for d in simulator.replay_patient(StatefulPredictor(bundle), "s", rows)]
    k = 6
    trunc = [d["p"] for d in simulator.replay_patient(StatefulPredictor(bundle), "s", rows[:k + 1])]
    causal = len(trunc) == k + 1 and np.allclose(trunc, full[:k + 1], atol=TOL)

    # B replay = observation only: replay one sealed-B patient; frozen stats must be unchanged
    manifest = cache_mod.load_manifest()
    pid2site = dict(zip(manifest.pid, manifest.site))
    b_pid = sorted(split_mod.split_cross_site(manifest, val_frac=0.2, seed=42)["B"])[0]
    idx = C.featureset_indices(FS)
    b_raw = cache_mod.load_feats_labels(pid2site[b_pid], b_pid)[0][:, idx].astype(np.float32)
    mu_before = bundle.mu.copy()
    simulator.replay_patient(StatefulPredictor(bundle), b_pid, b_raw)
    stats_unchanged = np.array_equal(bundle.mu, mu_before) and not bundle.mu.flags.writeable

    # frozen-only over ALL serve files (now incl app/simulator/metrics)
    forbidden = {"compute_norm_stats", "compute_fill_mean", "select_threshold",
                 "train_gru", "run_search", "fit", "tune"}
    used = set()
    serve_files = sorted((C.ROOT / "src/sepsis/serve").glob("*.py"))
    for f in serve_files:
        tree = ast.parse(f.read_text())
        used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        used |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    frozen_ok = not (forbidden & used)
    check(causal and stats_unchanged and frozen_ok,
          "#3 simulator causal + B observation-only",
          f"future-not-used(trunc==full[:k+1])={causal}, B-replay frozen-stats-unchanged="
          f"{stats_unchanged}, frozen-only({len(serve_files)} serve files)={frozen_ok}")

    print("\n=== H4s-b serving API gate ===")
    for ln in lines:
        print(ln)
    if not ok:
        print("\nH4s-b: FAIL — stopping.", file=sys.stderr)
        return 1
    print("\nH4s-b: PASS (4/4). H4s-c (Docker + K8s infra) is the next session.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
