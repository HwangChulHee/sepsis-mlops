"""H4 drift-loop gate — serving reads the ACTIVE bundle reference and runs drift end-to-end.

Closes the recommendation from h4/retrain/handoff_review v2 #1: the drift monitor must read
the reference FROM THE ACTIVE BUNDLE (so rollback restores the matching baseline), not a
standalone data/drift file. 5 asserts; all PASS -> loop wired.

    uv run python -m scripts.h4.h4_drift_loop_smoke
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

from sepsis import config as C

BUNDLE_DIR = str(Path(C.ROOT) / ".scratch_drift_bundle")
os.environ["SERVE_BUNDLE_DIR"] = BUNDLE_DIR          # active bundle the server serves from
os.environ["DRIFT_WINDOW_N"] = "150"                 # smaller window/calibration for the gate
os.environ["DRIFT_CAL_TRIALS"] = "60"

from sepsis.data import cache as cache_mod  # noqa: E402
from sepsis.data import split as split_mod
from sepsis.drift import reference as R  # noqa: E402

FS = "vitals"


def main() -> int:
    lines, ok = [], True

    def check(cond, label, detail):
        nonlocal ok
        if not cond:
            ok = False
        lines.append(f"[{'PASS' if cond else 'FAIL'}] {label}: {detail}")

    # Build a DISTINCT active-bundle reference (subset -> known n=1000) so we can prove the
    # server loads THIS file, not the full-A-train standalone data/drift reference.
    man = cache_mod.load_manifest()
    pid2site = dict(zip(man.pid, man.site))
    a_train = sorted(split_mod.split_cross_site(man, val_frac=0.2, seed=42)["A_train"])[:1000]
    ref = R.build_reference_from_pids(FS, a_train, pid2site)
    Path(BUNDLE_DIR).mkdir(parents=True, exist_ok=True)
    R.save_reference(ref, Path(BUNDLE_DIR) / "reference.npz")
    N_REF = ref.n_patients                                       # 1000 (distinct marker)

    from fastapi.testclient import TestClient

    from sepsis.drift.window import get_window, reset_window
    from sepsis.retrain import promote
    from sepsis.serve import app as appmod
    client = TestClient(appmod.app)

    # --- #2 insufficient window -> no test ---
    reset_window()
    r_insuff = client.get("/drift").json()

    # --- drive a drifted window: shift 4/9 features by +4σ on 200 patients ---
    ds = appmod.drift_state()                                    # loads ref + calibrates thr
    refmat = ds["ref"].summary
    std = np.nanstd(refmat, axis=0)
    rng = np.random.default_rng(0)
    cur = refmat[rng.integers(0, refmat.shape[0], size=200)].copy()
    for j in range(4):
        cur[:, j] = cur[:, j] + 4.0 * std[j]
    for i in range(cur.shape[0]):
        get_window().add(f"p{i}", cur[i])
    r_ok = client.get("/drift").json()

    # --- #1 reference comes from the ACTIVE BUNDLE (n==1000), not standalone data/drift ---
    loaded_n = appmod.drift_state()["ref"].n_patients
    ref_src_ok = loaded_n == N_REF == 1000
    check(ref_src_ok, "#1 drift reads ACTIVE BUNDLE reference (not data/drift)",
          f"server loaded reference n_patients={loaded_n} == bundle file n={N_REF} "
          f"(distinct subset -> proves SERVE_BUNDLE_DIR/reference.npz is the source)")

    # --- #2 ---
    insuff_ok = r_insuff.get("status") == "insufficient" and r_insuff["min_patients"] == 150
    check(insuff_ok, "#2 small window -> insufficient (no test run)",
          f"empty window -> {r_insuff.get('status')} (n={r_insuff.get('n_patients')}, "
          f"min={r_insuff.get('min_patients')})")

    # --- #3 sufficient -> detection with the watch/promote dict shape ---
    shape_ok = (r_ok.get("status") == "ok" and "dataset_drift_share" in r_ok
                and len(r_ok.get("features", [])) == 9
                and all({"feature", "value", "missing_js", "drift"} <= set(f) for f in r_ok["features"]))
    check(shape_ok, "#3 /drift -> detection (dataset_drift_share + per-feature shape)",
          f"status={r_ok.get('status')}, share={r_ok.get('dataset_drift_share'):.3f}, "
          f"n_features={len(r_ok.get('features', []))}")

    # --- #4 injected drift detected + detection consumable by promote (loop -> action) ---
    share = r_ok.get("dataset_drift_share", 0.0)
    det = {k: r_ok[k] for k in ("features", "dataset_drift_share", "methods")}
    rec = promote.promote([det, det], share_threshold=0.3, persistence=2)
    loop_ok = (share > 0.3 and isinstance(rec, promote.Recommendation)
               and rec.action == "investigate")
    check(loop_ok, "#4 injected drift detected + promote consumes detection -> investigate",
          f"share={share:.3f} (>0.3), promote(persisted) -> {rec.action} "
          f"(drifted: {rec.drifted_features})")

    # --- #5 serving drift path is Evidently-free (lean image preserved) ---
    lean = "evidently" not in sys.modules
    check(lean, "#5 drift loop is numpy-only (no Evidently imported in serving)",
          f"'evidently' in sys.modules = {not lean}")

    # cleanup
    import shutil
    shutil.rmtree(BUNDLE_DIR, ignore_errors=True)

    print("\n=== H4 drift-loop gate ===")
    for ln in lines:
        print(ln)
    if not ok:
        print("\nH4 drift-loop: FAIL — stopping.", file=sys.stderr)
        return 1
    print("\nH4 drift-loop: PASS (5/5). Serving observes drift against its active-bundle "
          "reference -> rollback restores the matching baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
