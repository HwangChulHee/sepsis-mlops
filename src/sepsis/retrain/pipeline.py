"""H4r-b — retrain pipeline (handoff H4r-b, DDD 결정 4). HUMAN-TRIGGERED.

Reuses the H1~H2 GRU path to retrain the DEPLOYED combo only (gru/vitals — not all 6),
reusing the frozen HP* and τ (no re-search). B is treated as operational data: setB is
split patient-level into B-retrain / B-holdout (no patient leak); training data =
A-train + B-retrain; train-only preprocessing stats recomputed on that set. Mask OFF,
no 0-fill (H1 rules). Invoked only when a human triggers it (promote.py never calls this).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

import numpy as np

from sepsis import config as C
from sepsis.data import cache as cache_mod
from sepsis.data import class_balance, missing, normalize
from sepsis.data import split as split_mod
from sepsis.serve.bundle import load_bundle
from sepsis.train import gru


def _git_commit() -> str:
    """Audit/MLflow-link identifier for the running code (mn1). `git rev-parse` does not
    detect a dirty tree, and non-repo / git-absent needs a fallback. Reproducibility is the
    `seed`'s job, NOT git_commit."""
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                             text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"                       # non-repo / git absent
    dirty = subprocess.run(["git", "status", "--porcelain"],
                           capture_output=True, text=True).stdout.strip()
    return sha + ("+dirty" if dirty else "")   # working tree dirty -> mark it


def _split_b(b_pids: list[str], holdout_frac: float, seed: int) -> tuple[list[str], list[str]]:
    """Patient-level B-retrain / B-holdout split (disjoint pids)."""
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(b_pids))
    n_hold = int(round(len(b_pids) * holdout_frac))
    arr = np.asarray(b_pids)
    return sorted(arr[perm[n_hold:]].tolist()), sorted(arr[perm[:n_hold]].tolist())  # retrain, holdout


@dataclass
class RetrainResult:
    featureset: str
    input_dim: int
    hp: dict
    tau: float
    stats: dict                          # NEW train-only mu/sigma/fill_mean/clip_lo/clip_hi
    model: gru.GRUm2m
    b_retrain: list[str]
    b_holdout: list[str]
    train_pids: list[str]
    aval_raw: list = field(default_factory=list)        # (raw_slice, labels) for A-val
    bholdout_data: list = field(default_factory=list)   # (transformed, labels) new-stats
    aval_data: list = field(default_factory=list)        # (transformed, labels) new-stats
    epochs: int = 0
    val_loss: float = float("nan")
    mask_on: bool = False                                # H1 rule: mask OFF
    run_id: str = ""                                     # MLflow run id (결정 3)
    git_commit: str = ""                                 # audit/link only (mn1; repro = seed)
    seed: int = 0                                        # B-split seed (MJ1 — reaches retrain.json)


def retrain(featureset: str = "vitals", *, holdout_frac: float = 0.3, seed: int = 42,
            max_epochs: int = 15, patience: int = 4, batch_size: int = 64,
            prog=None) -> RetrainResult:
    """Human-triggered retrain of the deployed gru/<featureset> on A-train + B-retrain."""
    old = load_bundle(featureset)                        # frozen HP*, τ (reused, no re-search)
    hp, tau = old.hp, old.tau
    idx = C.featureset_indices(featureset)
    lo, hi = normalize.clip_bounds(featureset)

    manifest = cache_mod.load_manifest()
    pid2site = dict(zip(manifest.pid, manifest.site))
    splits = split_mod.split_cross_site(manifest, val_frac=0.2, seed=seed)
    a_train, a_val, b_all = splits["A_train"], splits["A_val"], splits["B"]
    b_retrain, b_holdout = _split_b(b_all, holdout_frac, seed)
    train_pids = a_train + b_retrain

    def raw_of(pid):
        f, lab = cache_mod.load_feats_labels(pid2site[pid], pid)
        return f[:, idx].astype(np.float32), lab.astype(np.float32)

    raw = {p: raw_of(p) for p in (train_pids + a_val + b_holdout)}

    # train-only stats on A-train + B-retrain (mask OFF, fill = train mean, NOT 0)
    fill_mean = missing.compute_fill_mean([missing.ffill(raw[p][0]) for p in train_pids])
    mu, sigma = normalize.compute_norm_stats(
        [normalize.clip(missing.fill_mean(missing.ffill(raw[p][0]), fill_mean), lo, hi)
         for p in train_pids])
    stats = {"mu": mu, "sigma": sigma, "fill_mean": fill_mean, "clip_lo": lo, "clip_hi": hi}

    def transform(f):
        c = normalize.clip(missing.fill_mean(missing.ffill(f), fill_mean), lo, hi)
        return normalize.normalize(c, mu, sigma)

    train_data = [(transform(raw[p][0]), raw[p][1]) for p in train_pids]
    aval_data = [(transform(raw[p][0]), raw[p][1]) for p in a_val]
    bholdout_data = [(transform(raw[p][0]), raw[p][1]) for p in b_holdout]
    aval_raw = [raw[p] for p in a_val]                   # raw kept so old model can use OLD stats

    spw = float(class_balance.per_timestep_balance(
        [raw[p][1].astype(np.int8) for p in train_pids]).pos_weight)

    # Record the retrain as an MLflow run (결정 3, MJ-a): the console resolves a deep-link by
    # run_id, so the run MUST land in the SAME sqlite store as bundle.py (single source of
    # truth). experiment="retrain" keeps it separate from the h2 training runs; run_id is
    # globally unique within the store, so the deep-link resolves regardless of experiment.
    import mlflow
    mlflow.set_tracking_uri(f"sqlite:///{C.ROOT}/mlflow.db")
    mlflow.set_experiment("retrain")
    with mlflow.start_run() as run:
        res = gru.train_gru(train_data, aval_data, len(idx), hp, pos_weight=spw, seed=seed,
                            max_epochs=max_epochs, patience=patience, batch_size=batch_size, prog=prog)
        run_id = run.info.run_id
        git_commit = _git_commit()
        mlflow.log_params({"featureset": featureset, "seed": seed, **hp})
        mlflow.log_metrics({"epochs": float(res.n_epochs), "val_loss": float(res.best_val_loss)})

    return RetrainResult(featureset=featureset, input_dim=len(idx), hp=hp, tau=tau, stats=stats,
                         model=res.model, b_retrain=b_retrain, b_holdout=b_holdout,
                         train_pids=train_pids, aval_raw=aval_raw, bholdout_data=bholdout_data,
                         aval_data=aval_data, epochs=res.n_epochs, val_loss=res.best_val_loss,
                         mask_on=False, run_id=run_id, git_commit=git_commit, seed=seed)
