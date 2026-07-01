"""H4r-a gate — watch->action promotion + delayed-label backfill (signal layer).

3 programmatic asserts; all PASS -> H4r-a done. Any FAIL -> stop & report.

    uv run python -m scripts.h4.h4r_a_smoke
"""

from __future__ import annotations

import ast
import inspect
import sys

import numpy as np

from sepsis import config as C
from sepsis.data import cache as cache_mod
from sepsis.data import split as split_mod
from sepsis.eval import crosssite
from sepsis.retrain import backfill, promote
from sepsis.serve.bundle import load_bundle
from sepsis.train import gru

FS = "vitals"


def _det(share, n_drift=2, n_feat=9):
    feats = [{"feature": f"f{i}", "drift": i < n_drift} for i in range(n_feat)]
    return {"dataset_drift_share": share, "features": feats}


def main() -> int:
    lines, ok = [], True

    def check(cond, label, detail):
        nonlocal ok
        if not cond:
            ok = False
        lines.append(f"[{'PASS' if cond else 'FAIL'}] {label}: {detail}")

    # --- #1 promote returns recommendation only + no auto retrain/deploy (AST grep) ---
    rec = promote.promote([_det(0.5), _det(0.5)], share_threshold=0.3, persistence=2)
    rec_only = isinstance(rec, promote.Recommendation) and rec.action in ("investigate", "none")
    forbidden = {"pipeline", "deploy", "swap", "train_gru", "run_search", "fit"}
    used = set()
    sig_files = [C.ROOT / "src/sepsis/retrain/promote.py", C.ROOT / "src/sepsis/retrain/backfill.py"]
    for f in sig_files:
        tree = ast.parse(f.read_text())
        used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        used |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    present = sorted(forbidden & used)
    check(rec_only and not present, "#1 recommendation only, no auto retrain/deploy",
          f"returns Recommendation({rec.action}); signal-layer forbidden calls={present or 'none'}")

    # --- #2 drift-driven (share + persistence), NOT performance ---
    inv = promote.promote([_det(0.5), _det(0.5), _det(0.5)], share_threshold=0.3, persistence=2)
    oneoff = promote.promote([_det(0.0), _det(0.0), _det(0.5)], share_threshold=0.3, persistence=2)
    low = promote.promote([_det(0.1), _det(0.1)], share_threshold=0.3, persistence=2)
    no_perf_param = "performance" not in inspect.signature(promote.promote).parameters
    drift_driven = (inv.action == "investigate" and oneoff.action == "none"
                    and low.action == "none" and no_perf_param)
    check(drift_driven, "#2 drift-driven (share+persistence), not performance",
          f"persisted→{inv.action}(p={inv.persisted_cycles}), one-off→{oneoff.action}, "
          f"low→{low.action}, promote() has no performance param={no_perf_param}")

    # --- #3 delayed-label backfill (real labels, delay only) + aux perf + umbrella limit ---
    b = load_bundle(FS)
    frozen = {"mu": b.mu, "sigma": b.sigma, "fill_mean": b.fill_mean,
              "clip_lo": b.clip_lo, "clip_hi": b.clip_hi}
    man = cache_mod.load_manifest()
    pid2site = dict(zip(man.pid, man.site))
    b_pids = sorted(split_mod.split_cross_site(man, val_frac=0.2, seed=42)["B"])[:150]
    idx = C.featureset_indices(FS)
    transformed, labels_real, stay_end = [], [], []
    for pid in b_pids:
        raw, lab = cache_mod.load_feats_labels(pid2site[pid], pid)
        raw = raw[:, idx].astype(np.float32)
        transformed.append((crosssite._gru_transform_frozen(raw, frozen), lab.astype(np.float32)))
        labels_real.append(lab)                         # REAL setB SepsisLabel
        stay_end.append(float(len(lab)))                # stay end (hours)
    per_labels, per_probs, _, _ = gru.evaluate(b.model, transformed, batch_size=64)

    delay = 24.0
    arrivals = backfill.simulate_arrivals(stay_end, delay)
    now_early = float(np.quantile(arrivals, 0.4))        # only some labels arrived
    now_late = float(arrivals.max() + 1)                 # all arrived
    early = backfill.backfill_performance(per_labels, per_probs, b.tau, arrivals, now_early)
    late = backfill.backfill_performance(per_labels, per_probs, b.tau, arrivals, now_late)
    labels_are_real = all(np.array_equal(per_labels[i], labels_real[i].astype(np.float32))
                          for i in range(len(per_labels)))
    backfill_ok = (0 < early.n_available < late.n_available == late.n_total
                   and np.isfinite(late.utility) and labels_are_real
                   and late.performance_is_auxiliary and late.umbrella_seller_untagged)
    check(backfill_ok, "#3 delayed backfill (real labels, delay) + aux perf + umbrella limit",
          f"available {early.n_available}→{late.n_available}/{late.n_total} as time advances; "
          f"late util={late.utility:.4f} prauc={late.prauc:.4f}; labels_real={labels_are_real}; "
          f"aux={late.performance_is_auxiliary}, umbrella_untagged={late.umbrella_seller_untagged}")

    print("\n=== H4r-a signal-layer gate ===")
    for ln in lines:
        print(ln)
    if not ok:
        print("\nH4r-a: FAIL — stopping.", file=sys.stderr)
        return 1
    print("\nH4r-a: PASS (3/3). H4r-b (retrain pipeline) is the next session.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
