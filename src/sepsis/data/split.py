"""H1-b — patient-level split (결정 5).

Modes:
- unified:    A·B combined, random patient-level train/val/test (in-site reference).
- cross_site: A→B. 3-way — A-train / A-val / B(sealed). B is used ONLY at final
  evaluation: never in train, val, normalization stats, or pos_weight.

pid is globally unique (setA p0xxxxx, setB p1xxxxx), so pid alone identifies a patient.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def split_cross_site(manifest: pd.DataFrame, *, val_frac: float = 0.2, seed: int = 42) -> dict[str, list[str]]:
    a = manifest.loc[manifest.site == "training_setA", "pid"].to_numpy()
    b = manifest.loc[manifest.site == "training_setB", "pid"].to_numpy()
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(a))
    n_val = int(round(len(a) * val_frac))
    val = a[perm[:n_val]]
    train = a[perm[n_val:]]
    return {
        "A_train": sorted(train.tolist()),
        "A_val": sorted(val.tolist()),
        "B": sorted(b.tolist()),  # sealed
    }


def split_unified(manifest: pd.DataFrame, *, val_frac: float = 0.2, test_frac: float = 0.2,
                  seed: int = 42) -> dict[str, list[str]]:
    pids = manifest["pid"].to_numpy()
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(pids))
    n_test = int(round(len(pids) * test_frac))
    n_val = int(round(len(pids) * val_frac))
    test = pids[perm[:n_test]]
    val = pids[perm[n_test:n_test + n_val]]
    train = pids[perm[n_test + n_val:]]
    return {
        "train": sorted(train.tolist()),
        "val": sorted(val.tolist()),
        "test": sorted(test.tolist()),
    }


def train_split_name(mode: str) -> str:
    """Which split provides the train-only statistics (norm μ/σ, fill mean, pos_weight)."""
    return "A_train" if mode == "cross_site" else "train"
