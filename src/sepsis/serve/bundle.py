"""H4s-a — run bundle: atomic load of a single H2 GRU run (결정 1·4).

A serving bundle is ONE MLflow run's artifacts loaded together: model state_dict +
A-frozen preprocessing (μ/σ·fill·clip) + τ + input_dim + featureset. Mixing pieces
from different runs (e.g. vitals model with vitals_labs stats) is a train-serving skew
vector, so everything comes from a single run_id and is cross-checked for consistency.
Loaded stats are made immutable (read-only) — serving never recomputes them.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from sepsis import config as C


def set_alias(root, alias: str, target_name: str) -> None:
    """Point alias -> target_name (relative symlink), atomically when possible.

    Migrates a legacy real directory at the alias path to a symlink. os.replace gives an
    atomic swap for the symlink->symlink case (versioned 교체/롤백 원자성). Lives here (not in
    a script) so both the export script and retrain.deploy import it from the package."""
    root = Path(root)
    link = root / alias
    tmp = root / (alias + ".swap")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(target_name, tmp)                       # relative target (same dir)
    if link.exists() and link.is_dir() and not link.is_symlink():
        shutil.rmtree(link)                            # migrate legacy real dir
    os.replace(tmp, link)                              # atomic for symlink / nonexistent


# torch / GRUm2m 는 top-level 에서 끌어오지 않는다(결함 7): set_alias 만 쓰는 console 의 import
# 체인(service→deploy→bundle)이 torch 없이 import 되도록, 무게 import 는 사용 함수 내부로 lazy화.
# (from __future__ import annotations 덕에 아래 Bundle.model 의 GRUm2m 주석은 문자열로만 평가됨.)


@dataclass(frozen=True)
class Bundle:
    run_id: str
    featureset: str
    input_dim: int
    tau: float
    hp: dict
    mu: np.ndarray
    sigma: np.ndarray
    fill_mean: np.ndarray
    clip_lo: np.ndarray
    clip_hi: np.ndarray
    model: "GRUm2m"


def _freeze(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=np.float32)
    a.setflags(write=False)   # immutable — serving must not mutate frozen stats
    return a


def _assemble(run_id: str, meta: dict, z, state_dict) -> Bundle:
    """Build + consistency-check a Bundle from raw pieces (shared by both loaders)."""
    from sepsis.train.gru import GRUm2m   # lazy: torch 끌어옴 — serve 핫패스에서만(결함 7)

    featureset = meta["featureset"]
    input_dim = int(meta["input_dim"])
    hp = meta["hp"]
    model = GRUm2m(input_dim, hp["hidden"], hp["layers"], hp["dropout"])
    model.load_state_dict(state_dict)
    model.eval()

    mu, sigma = _freeze(z["mu"]), _freeze(z["sigma"])
    fill_mean = _freeze(z["fill_mean"])
    clip_lo, clip_hi = _freeze(z["clip_lo"]), _freeze(z["clip_hi"])

    # --- atomicity / consistency guard (불일치 번들 = skew → 정지) ---
    F = len(C.featureset_columns(featureset))
    errors = []
    if input_dim != F:
        errors.append(f"input_dim {input_dim} != featureset size {F} (mask-OFF expects F)")
    for name, arr in (("mu", mu), ("sigma", sigma), ("fill_mean", fill_mean),
                      ("clip_lo", clip_lo), ("clip_hi", clip_hi)):
        if arr.shape != (F,):
            errors.append(f"{name} shape {arr.shape} != ({F},)")
    if model.gru.input_size != input_dim:
        errors.append(f"model input_size {model.gru.input_size} != input_dim {input_dim}")
    if errors:
        raise ValueError(f"bundle mismatch (run {run_id}): " + "; ".join(errors))

    return Bundle(run_id=run_id, featureset=featureset, input_dim=input_dim,
                  tau=float(meta["tau"]), hp=hp, mu=mu, sigma=sigma, fill_mean=fill_mean,
                  clip_lo=clip_lo, clip_hi=clip_hi, model=model)


def load_bundle_from_dir(artifacts_dir) -> Bundle:
    """Load an exported bundle dir (meta.json + pre.npz + model.pt). Used in the container
    (no MLflow). The dir IS one exported run -> atomicity is intrinsic."""
    import torch   # lazy(결함 7)

    d = Path(artifacts_dir)
    meta = json.loads((d / "meta.json").read_text())
    z = np.load(d / "pre.npz")
    state = torch.load(d / "model.pt", weights_only=True)
    return _assemble(meta.get("run_id", str(d.name)), meta, z, state)


def load_bundle(featureset: str = "vitals", *, artifacts_dir: str | None = None,
                tracking_uri: str | None = None, experiment: str = "h2") -> Bundle:
    """Load the gru/<featureset> bundle. If artifacts_dir is given, load from that exported
    dir (container/portable); else atomically from a SINGLE MLflow run (local/dev)."""
    if artifacts_dir is not None:
        return load_bundle_from_dir(artifacts_dir)

    import mlflow
    import torch   # lazy(결함 7)
    from mlflow.artifacts import download_artifacts

    tracking_uri = tracking_uri or f"sqlite:///{C.ROOT}/mlflow.db"
    mlflow.set_tracking_uri(tracking_uri)
    client = mlflow.tracking.MlflowClient()
    exp = client.get_experiment_by_name(experiment)
    if exp is None:
        raise RuntimeError(f"MLflow experiment {experiment!r} not found")
    runs = mlflow.search_runs(experiment_ids=[exp.experiment_id])
    # segment=="h2c" disambiguates the H2 training run from the H3-c mask-ON run
    # (which also carries params.model=gru, featureset=vitals but has no preprocess bundle).
    sel = runs[(runs["params.model"] == "gru") & (runs["params.featureset"] == featureset)
               & (runs["params.segment"] == "h2c")]
    if len(sel) == 0:
        raise RuntimeError(f"no h2c gru/{featureset} run in experiment {experiment!r}")
    run_id = sel.iloc[0]["run_id"]

    def fetch(path):  # ALWAYS from the same run_id -> atomic bundle
        return download_artifacts(run_id=run_id, artifact_path=path, tracking_uri=tracking_uri)

    meta = json.loads(Path(fetch("preprocess.json")).read_text())
    z = np.load(fetch(f"preprocess/pre_{featureset}.npz"))
    state = torch.load(fetch(f"model/gru_{featureset}.pt"), weights_only=True)
    if meta["featureset"] != featureset:
        raise ValueError(f"run featureset {meta['featureset']!r} != requested {featureset!r}")
    return _assemble(run_id, meta, z, state)
