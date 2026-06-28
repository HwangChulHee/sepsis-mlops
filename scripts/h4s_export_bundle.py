"""H4s-c — export a serving bundle from MLflow to a portable dir (for the container).

MLflow's sqlite file store records ABSOLUTE host artifact paths, so the DB is not portable
into a container. This exports gru/<featureset> to deploy/artifacts/gru_<featureset>/
(model.pt + pre.npz + meta.json) — a self-contained dir the image COPYs and the app loads
via bundle.load_bundle_from_dir (no MLflow at runtime). One dir = one run = atomic bundle.

    uv run python -m scripts.h4s_export_bundle               # exports vitals + vitals_labs
    uv run python -m scripts.h4s_export_bundle vitals        # just one
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np

from sepsis import config as C

OUT_ROOT = C.ROOT / "deploy" / "artifacts"
TRACKING = f"sqlite:///{C.ROOT}/mlflow.db"


def export(featureset: str) -> Path:
    import mlflow
    from mlflow.artifacts import download_artifacts

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

    out = OUT_ROOT / f"gru_{featureset}"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pt, out / "model.pt")
    shutil.copyfile(npz, out / "pre.npz")
    (out / "meta.json").write_text(json.dumps(meta, indent=2))
    z = np.load(out / "pre.npz")  # sanity
    print(f"[export] gru_{featureset} -> {out.relative_to(C.ROOT)} "
          f"(run {run_id[:8]}, input_dim={meta['input_dim']}, mu{z['mu'].shape})")
    return out


def main() -> int:
    featuresets = sys.argv[1:] or ["vitals", "vitals_labs"]
    for fs in featuresets:
        export(fs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
