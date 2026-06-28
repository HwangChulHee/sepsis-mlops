"""H4r-b gate — retrain pipeline (B as operational data, in-distribution validation).

5 programmatic asserts; all PASS -> H4r-b done. Any FAIL -> stop & report.

    uv run python -m scripts.h4r_b_smoke
"""

from __future__ import annotations

import sys

import numpy as np

from sepsis import config as C
from sepsis.data import normalize
from sepsis.retrain import pipeline, validate
from sepsis.serve.bundle import load_bundle
from sepsis.util.progress import ProgressLogger

FS = "vitals"


def main() -> int:
    lines, ok = [], True

    def check(cond, label, detail):
        nonlocal ok
        if not cond:
            ok = False
        lines.append(f"[{'PASS' if cond else 'FAIL'}] {label}: {detail}")

    old = load_bundle(FS)
    prog = ProgressLogger(15 * 480, "h4r-b-retrain", "logs/h4r_b.log")  # rough total for ETA
    rr = pipeline.retrain(FS, holdout_frac=0.3, seed=42, max_epochs=15, patience=4, prog=prog)
    prog.done(f"epochs={rr.epochs} val_loss={rr.val_loss:.4f}")
    vr = validate.validate(rr, old)

    # --- #1 B patient-level retrain/holdout split, disjoint ---
    from sepsis.data import cache as cache_mod, split as split_mod
    b_all = set(split_mod.split_cross_site(cache_mod.load_manifest(), val_frac=0.2, seed=42)["B"])
    sr, sh = set(rr.b_retrain), set(rr.b_holdout)
    split_ok = (sr & sh == set() and sr | sh == b_all and len(sh) > 0 and len(sr) > 0)
    check(split_ok, "#1 B retrain/holdout patient-level disjoint",
          f"B-retrain={len(sr)} B-holdout={len(sh)} ∩=∅:{sr & sh == set()} ∪==B:{sr | sh == b_all}")

    # --- #2 train = A-train+B-retrain, train-only stats recomputed, deployed combo, HP*·τ reused ---
    a_train = set(split_mod.split_cross_site(cache_mod.load_manifest(), val_frac=0.2, seed=42)["A_train"])
    train_ok = set(rr.train_pids) == (a_train | sr)
    stats_recomputed = (rr.stats["mu"].shape == (rr.input_dim,)
                        and not np.allclose(rr.stats["mu"], old.mu))   # new train set -> new stats
    reuse_ok = rr.hp == old.hp and abs(rr.tau - old.tau) < 1e-12 and rr.featureset == FS
    check(train_ok and stats_recomputed and reuse_ok,
          "#2 A-train+B-retrain, train-only stats, deployed combo, HP*·τ reused",
          f"train==A_train∪B_retrain:{train_ok}; stats recomputed(μ≠old):{stats_recomputed}; "
          f"hp==old:{rr.hp == old.hp} τ==old:{abs(rr.tau - old.tau) < 1e-12} fs={rr.featureset}")

    # --- #3 validation = B-holdout perf + A-val (new vs old) both measured ---
    fin = np.isfinite
    val_measured = (fin(vr.bholdout_util) and fin(vr.bholdout_prauc)
                    and fin(vr.new_aval_util) and fin(vr.old_aval_util))
    check(val_measured, "#3 B-holdout perf + A-val no-regression measured",
          f"B-holdout util={vr.bholdout_util:.4f} prauc={vr.bholdout_prauc:.4f}; "
          f"A-val new util={vr.new_aval_util:.4f} vs old={vr.old_aval_util:.4f} "
          f"(no_regression={vr.no_regression})")

    # --- #4 cross-site claim absent, in-distribution flagged ---
    cs_ok = (vr.cross_site_claim is False and "in-distribution" in vr.distribution
             and "3rd site" in vr.distribution)
    check(cs_ok, "#4 no cross-site claim (in-distribution flagged)",
          f"cross_site_claim={vr.cross_site_claim}; dist='{vr.distribution[:60]}...'")

    # --- #5 mask OFF + no 0-fill (H1 rules) ---
    mask_off = rr.input_dim == len(C.featureset_columns(FS)) and rr.mask_on is False  # F not 2F
    # 0-fill check: an all-NaN feature row -> filled with TRAIN MEAN, not 0
    F = rr.input_dim
    allnan = np.full((1, F), np.nan, np.float32)
    lo, hi = rr.stats["clip_lo"], rr.stats["clip_hi"]
    from sepsis.data import missing
    z_mean = normalize.normalize(normalize.clip(missing.fill_mean(allnan, rr.stats["fill_mean"]), lo, hi),
                                 rr.stats["mu"], rr.stats["sigma"])
    z_zero = normalize.normalize(normalize.clip(np.zeros((1, F), np.float32), lo, hi),
                                 rr.stats["mu"], rr.stats["sigma"])
    no_zerofill = not np.allclose(z_mean, z_zero)        # fill-mean != 0-fill
    check(mask_off and no_zerofill, "#5 mask OFF + no 0-fill (H1 rules)",
          f"input_dim={rr.input_dim}=F (mask off), mask_on={rr.mask_on}; fill=train-mean≠0:{no_zerofill}")

    print("\n=== H4r-b retrain gate ===")
    for ln in lines:
        print(ln)
    print(f"\nretrain: epochs={rr.epochs}, B-retrain={len(sr)}/B-holdout={len(sh)}")
    print(f"B-holdout (new data, in-dist): util={vr.bholdout_util:.4f} PR-AUC={vr.bholdout_prauc:.4f}")
    print(f"A-val no-regression: new util={vr.new_aval_util:.4f} (PR-AUC {vr.new_aval_prauc:.4f}) "
          f"vs old util={vr.old_aval_util:.4f} (PR-AUC {vr.old_aval_prauc:.4f}) -> "
          f"{'OK' if vr.no_regression else 'REGRESSED'}")

    if not ok:
        print("\nH4r-b: FAIL — stopping.", file=sys.stderr)
        return 1
    print("\nH4r-b: PASS (5/5). H4r-c (safe versioned swap + rollback) is the next session.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
