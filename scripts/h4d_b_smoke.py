"""H4d-b gate — Evidently + window + watch + Grafana (= H4-drift complete).

5 programmatic asserts; all PASS -> H4-drift done.

    uv run python -m scripts.h4d_b_smoke
"""

from __future__ import annotations

import json
import sys

import numpy as np
from fastapi.testclient import TestClient

from sepsis import config as C
from sepsis.drift import detector as DET
from sepsis.drift import reference as R
from sepsis.drift import synthetic as S
from sepsis.drift import watch
from sepsis.drift.window import DriftWindow, get_window, reset_window

FS = "vitals"
REF_PATH = C.ROOT / "data" / "drift" / f"reference_{FS}.npz"


def main() -> int:
    lines, ok = [], True

    def check(cond, label, detail):
        nonlocal ok
        if not cond:
            ok = False
        lines.append(f"[{'PASS' if cond else 'FAIL'}] {label}: {detail}")

    ref = R.load_reference(REF_PATH) if REF_PATH.exists() else R.save_reference(R.build_reference(FS)) and R.load_reference(REF_PATH)
    cols = ref.cols
    F = len(cols)

    # --- #1 window: (pid,row) collected via app, per-patient aggregation, separate, insufficient ---
    reset_window()
    from sepsis.serve.app import app, state
    with TestClient(app) as client:
        pred_state = state()
        for pid in ("w1", "w2"):
            for t in range(3):
                feats = {c: 70.0 + t for c in cols if c != "Temp"}     # Temp omitted -> NaN
                client.post("/predict", json={"patient_id": pid, "features": feats})
        win = get_window()
        summ = win.patient_summary()
        win_ok = (isinstance(win, DriftWindow) and win.n_patients() == 2
                  and summ.shape == (2, F) and win is not pred_state["pred"]
                  and not win.ready(1000))                              # insufficient-data
    check(win_ok, "#1 window (pid,row) per-patient + separate + insufficient",
          f"n_patients={win.n_patients()} summary={summ.shape} sep={win is not pred_state['pred']} "
          f"ready(1000)={win.ready(1000)}")

    # --- #2 Evidently report, method PINNED to distance (no KS) ---
    ed = DET.evidently_distances(ref, ref.summary[:500])
    methods = sorted({v["method"] for v in ed.values()})
    method_ok = len(ed) == F and "ks" not in methods and set(methods) <= {"psi", "jensenshannon"}
    check(method_ok, "#2 Evidently report + num_stattest pinned (no KS fallback)",
          f"{len(ed)} columns, methods={methods}")

    # calibrate both engines (Evidently operational; distance.py = H4d-a oracle)
    evi_thr = DET.calibrate(ref, alpha=0.05, window_n=500, n_trials=40, seed=0)
    h4a_thr = S.calibrate(ref, alpha=0.05, window_n=500, n_trials=300, seed=0)

    # --- #5 (compute before #3 so we have a detection to publish) injection agreement ---
    rng = np.random.default_rng(7)
    cur = S.bootstrap(ref.summary, 500, rng)
    hr_i = cols.index("HR")
    injected = S.inject_mean_shift(cur, hr_i, delta=float(np.nanstd(ref.summary[:, hr_i])))
    evi_det = DET.detect(ref, injected, evi_thr)
    h4a_det = {f["feature"]: f for f in S.detect(ref, injected, h4a_thr)}
    evi_hr = next(f for f in evi_det["features"] if f["feature"] == "HR")
    agree = evi_hr["value_drift"] and h4a_det["HR"]["value_drift"]
    check(agree, "#5 synthetic injection — Evidently agrees with H4d-a engine",
          f"HR: evidently value={evi_hr['value']:.3f}>thr{evi_hr['value_thr']:.3f}={evi_hr['value_drift']}, "
          f"h4d-a value={h4a_det['HR']['value']:.3f}={h4a_det['HR']['value_drift']}")

    # --- #3 watch signal to Prometheus, NO alarm/action ---
    watch.publish(evi_det)
    text = watch.render()[0].decode()
    from prometheus_client import Gauge
    watch_names = {g._name for g in vars(watch).values() if isinstance(g, Gauge)}
    needed = ["drift_value_distance", "drift_missing_js", "drift_state", "drift_dataset_share"]
    have = all(n in text for n in needed)
    pub_fns = {n for n in dir(watch) if callable(getattr(watch, n)) and not n.startswith("_")}
    no_action_fn = not (pub_fns & {"promote", "escalate", "alarm", "action", "retrain"})
    # boundary: watch metric NAMES (not HELP text) carry no alarm/action/promote signal
    no_action_metric = not any(any(w in n for w in ("alarm", "action", "promote", "retrain"))
                               for n in watch_names)
    check(have and no_action_fn and no_action_metric,
          "#3 watch Prometheus signal, no alarm/action (scope boundary)",
          f"watch metrics present={have}, names={sorted(watch_names)}, no-action-fn={no_action_fn}, "
          f"no-action-metric={no_action_metric}")

    # --- #4 Grafana provisioning JSON valid + required panels ---
    dash = json.loads((C.ROOT / "deploy/grafana/dashboards/drift.json").read_text())
    exprs = {t["expr"] for p in dash.get("panels", []) for t in p.get("targets", [])}
    req_metrics = {"drift_value_distance", "drift_state", "drift_dataset_share"}
    prov_ok = ((C.ROOT / "deploy/grafana/provisioning/dashboards/drift.yaml").exists()
               and (C.ROOT / "deploy/grafana/provisioning/datasources/prometheus.yaml").exists())
    grafana_ok = (dash.get("panels") and req_metrics <= {e.split("(")[-1].rstrip(")").split("[")[0]
                                                          if "(" in e else e for e in exprs}
                  and prov_ok)
    # simpler containment: each required metric appears in some expr
    grafana_ok = bool(dash.get("panels")) and all(any(m in e for e in exprs) for m in req_metrics) and prov_ok
    check(grafana_ok, "#4 Grafana provisioning JSON valid + panels",
          f"panels={len(dash.get('panels', []))}, required metrics in targets={all(any(m in e for e in exprs) for m in req_metrics)}, provisioning={prov_ok}")

    print("\n=== H4d-b drift detector gate ===")
    for ln in lines:
        print(ln)
    if not ok:
        print("\nH4d-b: FAIL — stopping.", file=sys.stderr)
        return 1
    print("\nH4d-b: PASS (5/5). H4-drift COMPLETE (a+b). Next: H4-retrain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
