"""H4s-c gate — Docker + K8s infra (h4_serving_handoff.md H4s-c). = H4-serving complete.

3 programmatic asserts:
  1. docker build succeeds (image self-contained: lean deps + exported bundle).
  2. kubectl --dry-run=client validates the manifests.
  3. RUN (ConfigMap) -> SERVE_BUNDLE_DIR -> atomic bundle load (run swap keeps atomicity).

    uv run python -m scripts.h4s_c_smoke

Prereq: scripts/h4s_export_bundle.py has populated deploy/artifacts/ (this runner does it).
A running cluster (minikube) is needed for #2's client validation.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

from sepsis import config as C
from sepsis.serve.bundle import load_bundle_from_dir

ROOT = C.ROOT
K8S = ROOT / "deploy" / "k8s"
IMAGE = "sepsis-serving:h4s"


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, **kw)


def main() -> int:
    lines, ok = [], True

    def check(cond, label, detail):
        nonlocal ok
        if not cond:
            ok = False
        lines.append(f"[{'PASS' if cond else 'FAIL'}] {label}: {detail}")

    # ensure exported bundles present (build COPYs them)
    import scripts.h4s_export_bundle as exporter
    for fs in ("vitals", "vitals_labs"):
        exporter.export(fs)

    # --- #1 docker build ---
    b = run(["docker", "build", "-f", "deploy/Dockerfile", "-t", IMAGE, "."])
    build_ok = b.returncode == 0
    tail = (b.stderr or b.stdout).strip().splitlines()[-1:] if not build_ok else ["built"]
    check(build_ok, "#1 docker build", f"rc={b.returncode} ({IMAGE}) {tail}")

    # --- #2 kubectl --dry-run=client validates manifests ---
    files = ["configmap.yaml", "deployment.yaml", "service.yaml"]
    dr_results, dr_ok = [], True
    for f in files:
        r = run(["kubectl", "apply", "--dry-run=client", "-f", str(K8S / f)])
        dr_results.append(f"{f}:{'ok' if r.returncode == 0 else 'FAIL'}")
        if r.returncode != 0:
            dr_ok = False
            dr_results.append((r.stderr or r.stdout).strip().splitlines()[-1:][0] if (r.stderr or r.stdout) else "")
    check(dr_ok, "#2 kubectl --dry-run=client", " ".join(dr_results))

    # --- #3 RUN -> SERVE_BUNDLE_DIR -> atomic bundle ---
    cm = yaml.safe_load((K8S / "configmap.yaml").read_text())
    dep = yaml.safe_load((K8S / "deployment.yaml").read_text())
    run_val = cm["data"]["RUN"]
    env = {e["name"]: e for e in dep["spec"]["template"]["spec"]["containers"][0]["env"]}
    # RUN sourced from ConfigMap; SERVE_BUNDLE_DIR composed via $(RUN)
    run_from_cm = env.get("RUN", {}).get("valueFrom", {}).get("configMapKeyRef", {}).get("key") == "RUN"
    bdir_tmpl = env.get("SERVE_BUNDLE_DIR", {}).get("value", "")
    uses_run = "$(RUN)" in bdir_tmpl
    # functional: each RUN value -> its atomic exported bundle (featureset matches name)
    link_ok = run_from_cm and uses_run
    fmap = {"gru_vitals": "vitals", "gru_vitals_labs": "vitals_labs"}
    details = []
    for run_name, fs in fmap.items():
        resolved = bdir_tmpl.replace("$(RUN)", run_name)              # e.g. /app/deploy/artifacts/gru_vitals
        local = ROOT / Path(resolved).relative_to("/app")             # map container path -> repo
        try:
            bundle = load_bundle_from_dir(local)
            atomic = (bundle.featureset == fs and bundle.input_dim == len(C.featureset_columns(fs)))
            details.append(f"{run_name}->fs={bundle.featureset}({'ok' if atomic else 'MISMATCH'})")
            link_ok = link_ok and atomic
        except Exception as e:  # noqa: BLE001
            link_ok = False
            details.append(f"{run_name}->ERR {e}")
    # configured RUN must be one we can resolve
    link_ok = link_ok and run_val in fmap
    check(link_ok, "#3 RUN->SERVE_BUNDLE_DIR->atomic bundle",
          f"RUN={run_val} from-configmap={run_from_cm} dir='{bdir_tmpl}' uses$(RUN)={uses_run}; "
          + " ".join(details))

    print("\n=== H4s-c infra gate ===")
    for ln in lines:
        print(ln)
    if not ok:
        print("\nH4s-c: FAIL — stopping.", file=sys.stderr)
        return 1
    print("\nH4s-c: PASS (3/3). H4-serving COMPLETE (a+b+c).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
