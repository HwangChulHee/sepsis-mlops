"""H4s-a — run bundle: atomic load of a single H2 GRU run (결정 1·4).

A serving bundle is ONE MLflow run's artifacts loaded together: model state_dict +
A-frozen preprocessing (μ/σ·fill·clip) + τ + input_dim + featureset. Mixing pieces
from different runs (e.g. vitals model with vitals_labs stats) is a train-serving skew
vector, so everything comes from a single run_id and is cross-checked for consistency.
Loaded stats are made immutable (read-only) — serving never recomputes them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch

from sepsis import config as C
from sepsis.train.gru import GRUm2m


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
    model: GRUm2m


def _freeze(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=np.float32)
    a.setflags(write=False)   # immutable — serving must not mutate frozen stats
    return a


def load_bundle(featureset: str = "vitals", *, tracking_uri: str | None = None,
                experiment: str = "h2") -> Bundle:
    """Atomically load the gru/<featureset> bundle from a SINGLE MLflow run."""
    import mlflow
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

    input_dim = int(meta["input_dim"])
    hp = meta["hp"]
    model = GRUm2m(input_dim, hp["hidden"], hp["layers"], hp["dropout"])
    model.load_state_dict(state)
    model.eval()

    mu, sigma = _freeze(z["mu"]), _freeze(z["sigma"])
    fill_mean = _freeze(z["fill_mean"])
    clip_lo, clip_hi = _freeze(z["clip_lo"]), _freeze(z["clip_hi"])

    # --- atomicity / consistency guard (불일치 번들 = skew → 정지) ---
    F = len(C.featureset_columns(featureset))
    errors = []
    if meta["featureset"] != featureset:
        errors.append(f"json featureset {meta['featureset']!r} != {featureset!r}")
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
