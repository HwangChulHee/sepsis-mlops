"""H4s-c/H4r-c — export a serving bundle from MLflow to a portable VERSIONED dir.

MLflow's sqlite file store records ABSOLUTE host artifact paths, so the DB is not portable
into a container. This exports gru/<featureset> to a VERSIONED dir
deploy/artifacts/gru_<featureset>@<version>/ (model.pt + pre.npz + meta.json + reference.npz)
and maintains an ACTIVE ALIAS deploy/artifacts/gru_<featureset> (relative symlink → active
version) so H4s-c serving (which hardcodes gru_<featureset>) keeps working while H4r-c can
add new versions + roll back without overwriting the live bundle. One version dir = one
atomic bundle (model+stats+τ+reference, same version). reference.npz is in the bundle so it
moves/rolls back WITH the model (drift monitor reads the active alias's reference.npz).

    uv run python -m scripts.h4.h4s_export_bundle               # exports vitals + vitals_labs
    uv run python -m scripts.h4.h4s_export_bundle vitals        # just one
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np

from sepsis import config as C
from sepsis.serve.bundle import set_alias  # alias helper lives in the package (not here)

OUT_ROOT = C.ROOT / "deploy" / "artifacts"
TRACKING = C.mlflow_uri()


def export(featureset: str, version: str | None = None, *, link_alias: bool = True) -> Path:
    import mlflow
    from mlflow.artifacts import download_artifacts

    from sepsis.drift import reference as R

    mlflow.set_tracking_uri(TRACKING)
    cl = mlflow.tracking.MlflowClient()
    exp = cl.get_experiment_by_name("h2")
    runs = mlflow.search_runs(experiment_ids=[exp.experiment_id])
    sel = runs[(runs["params.model"] == "gru") & (runs["params.featureset"] == featureset)
               & (runs["params.segment"] == "h2c")]
    if len(sel) == 0:
        raise RuntimeError(f"no h2c gru/{featureset} run")
    run_id = sel.iloc[0]["run_id"]

    def fetch(p):
        return download_artifacts(run_id=run_id, artifact_path=p, tracking_uri=TRACKING)

    meta = json.loads(Path(fetch("preprocess.json")).read_text())
    meta["run_id"] = run_id
    npz = fetch(f"preprocess/pre_{featureset}.npz")
    pt = fetch(f"model/gru_{featureset}.pt")

    version = version or time.strftime("v%Y%m%d-%H%M%S")
    meta["version"] = version
    out = OUT_ROOT / f"gru_{featureset}@{version}"
    if out.exists():                                   # re-export of THIS version only (others kept)
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pt, out / "model.pt")
    shutil.copyfile(npz, out / "pre.npz")
    (out / "meta.json").write_text(json.dumps(meta, indent=2))
    # reference IN the bundle (baseline = A-train), so it moves/rolls back with the model
    R.save_reference(R.build_reference(featureset), out / "reference.npz")
    if link_alias:
        set_alias(OUT_ROOT, f"gru_{featureset}", out.name)
    z = np.load(out / "pre.npz")  # sanity
    print(f"[export] gru_{featureset}@{version} -> {out.relative_to(C.ROOT)} "
          f"(run {run_id[:8]}, input_dim={meta['input_dim']}, mu{z['mu'].shape}); alias gru_{featureset}")
    return out


def main() -> int:
    featuresets = sys.argv[1:] or ["vitals", "vitals_labs"]
    for fs in featuresets:
        export(fs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
