"""H1-b — pos_weight input (부속 결정).

Per-timestep positive ratio over the TRAIN split (A-train in cross_site), padding
excluded (real timesteps only). Produces the pos_weight INPUT for H2's per-timestep
BCE; application is H2. Computed train-only (never touches val/test/B).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class BalanceStats:
    n_pos: int
    n_total: int
    pos_ratio: float          # per-timestep positive fraction
    pos_weight: float         # neg / pos


def per_timestep_balance(train_labels: list[np.ndarray]) -> BalanceStats:
    n_pos = int(sum(int((lab == 1).sum()) for lab in train_labels))
    n_total = int(sum(int(lab.shape[0]) for lab in train_labels))
    n_neg = n_total - n_pos
    pos_ratio = n_pos / n_total if n_total else float("nan")
    pos_weight = n_neg / n_pos if n_pos else float("inf")
    return BalanceStats(n_pos=n_pos, n_total=n_total, pos_ratio=pos_ratio, pos_weight=pos_weight)
