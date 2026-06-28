"""H4s-a gate — serving core (h4_serving_handoff.md H4s-a). ★ train-serving skew 방어선.

5 programmatic asserts; all PASS -> H4s-a complete. Any FAIL -> stop & report.

    uv run python -m scripts.h4s_smoke
"""

from __future__ import annotations

import ast
import sys
import threading
from pathlib import Path

import numpy as np
import torch

from sepsis import config as C
from sepsis.data import missing, normalize
from sepsis.serve import bundle as bundle_mod
from sepsis.serve.predictor import StatefulPredictor
from sepsis.serve.preprocess_rt import StreamPreprocessor

TOL = 1e-6
FS = "vitals"


def synth_raw(T, F, seed):
    """Raw featureset rows with NaN edges: leading-missing, fully-missing, middle-missing, sparse."""
    rng = np.random.default_rng(seed)
    raw = rng.normal(80.0, 20.0, size=(T, F)).astype(np.float32)
    raw[rng.random((T, F)) < 0.2] = np.nan   # sparse missing
    raw[0:3, 0] = np.nan                       # leading missing (feat 0)
    raw[4:7, 5 % F] = np.nan                   # middle missing (feat 5)
    raw[:, 2] = np.nan                         # fully missing (feat 2)
    return raw


def batch_transform(raw, b):
    """Training pipeline (H2-c transform) with the bundle's frozen A constants."""
    a = missing.fill_mean(missing.ffill(raw), b.fill_mean)
    a = normalize.clip(a, b.clip_lo, b.clip_hi)
    return normalize.normalize(a, b.mu, b.sigma)


def main() -> int:
    lines, ok = [], True

    def check(cond, label, detail):
        nonlocal ok
        if not cond:
            ok = False
        lines.append(f"[{'PASS' if cond else 'FAIL'}] {label}: {detail}")

    b = bundle_mod.load_bundle(FS)
    F = len(C.featureset_columns(FS))

    # --- #1 bundle atomicity (single run, consistent shapes) ---
    atomic = (b.input_dim == F and b.mu.shape == (F,) and b.sigma.shape == (F,)
              and b.fill_mean.shape == (F,) and b.clip_lo.shape == (F,)
              and b.clip_hi.shape == (F,) and b.model.gru.input_size == b.input_dim
              and b.featureset == FS and not b.mu.flags.writeable)
    check(atomic, "#1 bundle atomicity (single run_id, consistent, immutable)",
          f"run={b.run_id[:8]} featureset={b.featureset} input_dim={b.input_dim}=F "
          f"stats(F,)+model.input_size match; stats immutable={not b.mu.flags.writeable}")

    # --- #2 train-serving bit-identical (incl. missing rows, np.nan, no 0-fill, no mask) ---
    raw = synth_raw(12, F, seed=1)
    z_batch = batch_transform(raw, b)
    pre = StreamPreprocessor(b)
    z_stream = np.stack([pre.step("p", raw[t]) for t in range(raw.shape[0])])
    max_d = float(np.max(np.abs(z_batch - z_stream)))
    # 0-fill counterfactual: missing -> 0 (instead of train mean) would differ
    z_zero = normalize.normalize(
        normalize.clip(np.where(np.isnan(missing.ffill(raw)), 0.0, missing.ffill(raw)),
                       b.clip_lo, b.clip_hi), b.mu, b.sigma)
    not_zerofill = not np.allclose(z_stream, z_zero, atol=TOL)
    no_mask = z_stream.shape[1] == F
    check(max_d <= TOL and not_zerofill and no_mask,
          "#2 train-serving bit-identical",
          f"max|Δ batch-vs-stream|={max_d:.2e}(<=1e-6), differs-from-0fill={not_zerofill}, "
          f"dim={z_stream.shape[1]}=F (mask off)")

    # --- #3 stateful == full re-input (causal) ---
    T = 12
    x = torch.randn(1, T, F)
    b.model.eval()
    with torch.no_grad():
        full = b.model.forward(x).reshape(-1)
        h, step = None, []
        for t in range(T):
            lg, h = b.model.forward_state(x[:, t:t + 1, :], h)
            step.append(lg.reshape(-1)[-1].item())
    step = torch.tensor(step)
    sd = float((full - step).abs().max())
    check(sd <= 1e-5, "#3 stateful == full re-input (causal)",
          f"max|Δ full-vs-step|={sd:.2e} (<=1e-5)")

    # --- #4 patient isolation + concurrency ---
    rawA, rawB = synth_raw(10, F, seed=2), synth_raw(10, F, seed=3)

    def run(pred, pid, rows):
        return [pred.predict(pid, rows[t])["p"] for t in range(rows.shape[0])]

    pA = run(StatefulPredictor(b), "A", rawA)
    pB = run(StatefulPredictor(b), "B", rawB)
    # isolation: interleaved on ONE predictor
    p2 = StatefulPredictor(b)
    iA, iB = [], []
    for t in range(10):
        iA.append(p2.predict("A", rawA[t])["p"])
        iB.append(p2.predict("B", rawB[t])["p"])
    isolated = np.allclose(iA, pA, atol=TOL) and np.allclose(iB, pB, atol=TOL)
    # concurrency: two threads, fresh predictor
    p3 = StatefulPredictor(b)
    out = {}
    def work(pid, rows):
        out[pid] = run(p3, pid, rows)
    tA = threading.Thread(target=work, args=("A", rawA))
    tB = threading.Thread(target=work, args=("B", rawB))
    tA.start(); tB.start(); tA.join(); tB.join()
    concurrent = np.allclose(out["A"], pA, atol=TOL) and np.allclose(out["B"], pB, atol=TOL)
    lock_ok = (p3._lock("A") is p3._lock("A")) and (p3._lock("A") is not p3._lock("B"))
    check(isolated and concurrent and lock_ok, "#4 patient isolation + concurrency",
          f"interleave-isolated={isolated}, concurrent-2patients-match={concurrent}, "
          f"per-pid-lock={lock_ok}")

    # --- #5 frozen-only (serve/*.py never calls fit/select/compute_*) ---
    forbidden = {"compute_norm_stats", "compute_fill_mean", "select_threshold",
                 "train_gru", "run_search", "fit", "tune"}
    used = set()
    serve_files = sorted((C.ROOT / "src/sepsis/serve").glob("*.py"))
    for f in serve_files:
        tree = ast.parse(f.read_text())
        used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        used |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    present = sorted(forbidden & used)
    check(not present, "#5 frozen-only (no fit/select/compute_* in serve)",
          f"scanned {len(serve_files)} serve files; forbidden found={present or 'none'}")

    print("\n=== H4s-a serving core gate ===")
    for ln in lines:
        print(ln)
    if not ok:
        print("\nH4s-a: FAIL — stopping.", file=sys.stderr)
        return 1
    print("\nH4s-a: PASS (5/5). H4s-b (FastAPI + simulator + metrics) is the next session.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
