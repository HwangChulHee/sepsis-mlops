"""H4r-c gate — safe versioned swap + rollback (= MLOps loop closed).

5 programmatic asserts; all PASS -> H4-retrain complete = MLOps loop closed.

    uv run python -m scripts.h4r_c_smoke
"""

from __future__ import annotations

import ast
import sys
from types import SimpleNamespace

import scripts.h4s_export_bundle as export_mod

from sepsis import config as C
from sepsis.retrain import deploy, pipeline, validate
from sepsis.serve.bundle import load_bundle, load_bundle_from_dir
from sepsis.util.progress import ProgressLogger

FS = "vitals"
ROOT = C.ROOT / "deploy" / "artifacts"


def main() -> int:
    lines, ok = [], True

    def check(cond, label, detail):
        nonlocal ok
        if not cond:
            ok = False
        lines.append(f"[{'PASS' if cond else 'FAIL'}] {label}: {detail}")

    old = load_bundle(FS)

    # baseline v0 (versioned + alias + reference in bundle)
    v0 = export_mod.export(FS, version="v0-base")
    serve_ok = False
    try:
        b_alias = load_bundle_from_dir(ROOT / f"gru_{FS}")     # alias -> v0
        serve_ok = b_alias.featureset == FS and b_alias.input_dim == 9
    except Exception:
        serve_ok = False

    # short real retrain -> v1
    prog = ProgressLogger(2 * 480, "h4r-c-retrain", "logs/h4r_c.log")
    rr = pipeline.retrain(FS, holdout_frac=0.3, seed=42, max_epochs=2, patience=2, prog=prog)
    prog.done(f"epochs={rr.epochs}")
    vr = validate.validate(rr, old)   # real: a 2-epoch model is undertrained -> gate blocks it (#3)
    v1 = deploy.materialize(rr, "v1-retrain", validation=vr, root=ROOT)   # MJ-b: validation 필수
    PASS_VAL = SimpleNamespace(no_regression=True)   # swap-mechanics tests (#2/#4): validation passes
    # (a real 15-epoch retrain passes — see H4r-b: new util 0.4023 vs old 0.4087)

    # --- #1 versioned bundle, prev preserved, live not overwritten, alias serves ---
    both_exist = v0.exists() and v1.exists() and v0.name != v1.name
    check(both_exist and serve_ok,
          "#1 versioned + prev preserved + alias serves",
          f"v0={v0.name} v1={v1.name} both_exist={both_exist}; alias load (기존 서빙) ok={serve_ok}")

    # --- #3 swap requires validation + human approval (build before #2's swap) ---
    raised_unapproved = raised_badval = False
    try:
        deploy.swap(FS, v1, validation=vr, approved=False, root=ROOT)
    except PermissionError:
        raised_unapproved = True
    try:
        deploy.swap(FS, v1, validation=SimpleNamespace(no_regression=False), approved=True, root=ROOT)
    except ValueError:
        raised_badval = True
    # auto-path grep: signal/retrain modules never call deploy.swap
    forbidden = {"swap", "set_active", "set_alias", "rollback"}
    used = set()
    sig_files = [C.ROOT / "src/sepsis/retrain/promote.py", C.ROOT / "src/sepsis/retrain/backfill.py",
                 C.ROOT / "src/sepsis/retrain/pipeline.py"]
    for f in sig_files:
        tree = ast.parse(f.read_text())
        used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        used |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    no_auto = not (forbidden & used)
    check(raised_unapproved and raised_badval and no_auto,
          "#3 swap needs validation + human approval, no auto path",
          f"approved=False→raise:{raised_unapproved}, bad-validation→raise:{raised_badval}, "
          f"signal/retrain auto-call deploy={'none' if no_auto else sorted(forbidden & used)}")

    # --- #2 swap (approved+validated) then rollback ---
    prev = deploy.swap(FS, v1, validation=PASS_VAL, approved=True, root=ROOT)   # -> active v1
    active_after_swap = deploy.active_version(FS, root=ROOT)
    deploy.rollback(FS, prev, root=ROOT)                                  # -> back to v0
    active_after_rollback = deploy.active_version(FS, root=ROOT)
    swap_ok = (active_after_swap == v1.name and prev == v0.name
               and active_after_rollback == v0.name)
    check(swap_ok, "#2 alias swap + rollback",
          f"swap→{active_after_swap}, prev={prev}, rollback→{active_after_rollback}")

    # --- #4 reference in bundle moves/rolls back with model; drift reads active alias ---
    deploy.swap(FS, v1, validation=PASS_VAL, approved=True, root=ROOT)
    ref_v1 = deploy.active_reference(FS, root=ROOT)        # via alias -> v1 reference (A+B-retrain)
    deploy.rollback(FS, v0.name, root=ROOT)
    ref_v0 = deploy.active_reference(FS, root=ROOT)        # via alias -> v0 reference (A-train)
    # v1 trained on A-train+B-retrain (more patients) than v0 (A-train only)
    ref_consistent = (ref_v1.n_patients > ref_v0.n_patients
                      and deploy.active_reference_path(FS, root=ROOT) == ROOT / f"gru_{FS}" / "reference.npz")
    check(ref_consistent,
          "#4 reference in bundle moves/rolls back; drift reads active alias",
          f"v1 ref n={ref_v1.n_patients} (A+B-retrain) > v0 ref n={ref_v0.n_patients} (A-train); "
          f"after rollback active reference = v0; drift loads {deploy.active_reference_path(FS, root=ROOT).name}")

    # --- #5 atomic bundle (each version self-consistent: model+stats+τ+reference) ---
    atomic = True
    for vdir in (v0, v1):
        try:
            bd = load_bundle_from_dir(vdir)
            has_ref = (vdir / "reference.npz").exists()
            atomic = atomic and bd.featureset == FS and bd.input_dim == 9 and has_ref
        except Exception:
            atomic = False
    check(atomic, "#5 atomic bundle per version (model+stats+τ+reference)",
          f"v0 & v1 each load as consistent bundle + reference.npz present={atomic}")

    print("\n=== H4r-c safe-swap gate ===")
    for ln in lines:
        print(ln)
    if not ok:
        print("\nH4r-c: FAIL — stopping.", file=sys.stderr)
        return 1
    print("\nH4r-c: PASS (5/5). H4-retrain COMPLETE = MLOps loop CLOSED (serve→drift→retrain→swap→serve).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
