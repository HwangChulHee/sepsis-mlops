"""H3-b step 2 — frozen-only B scoring (결정 2). ★ NO B leakage.

B is transformed with A-FROZEN artifacts only (μ/σ·fill·clip·τ from H2/MLflow). This
module deliberately imports ONLY *apply* functions — never the *fitting* functions
(`compute_norm_stats`, `compute_fill_mean`, `select_threshold`). The H3-b runner greps
this file to assert those names are absent, and asserts the stats used are bit-equal to
the loaded artifacts. B never reaches train/tune/select.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch
from sklearn.metrics import average_precision_score

from sepsis import config as C
from sepsis.data import cache as cache_mod, features, missing, normalize, sequence
from sepsis.eval import threshold, utility
from sepsis.train import gru, tree


# --------------------------------------------------------------------------
# B data (NaN-preserved raw, featureset-sliced). One-time open of sealed setB.
# --------------------------------------------------------------------------
def load_b_raw(featureset: str, manifest, b_pids) -> list[tuple[np.ndarray, np.ndarray]]:
    """Per-patient (raw featureset slice T×F NaN-preserved, labels T). setB only."""
    idx = C.featureset_indices(featureset)
    pid2site = dict(zip(manifest.pid, manifest.site))
    out = []
    for pid in sorted(b_pids):
        f, lab = cache_mod.load_feats_labels(pid2site[pid], pid)
        out.append((f[:, idx].astype(np.float32), lab))
    return out


# --------------------------------------------------------------------------
# frozen transforms (APPLY only — no fitting)
# --------------------------------------------------------------------------
def _tree_summary(raw: np.ndarray) -> np.ndarray:
    return features.lookback_summary(raw)  # NaN-native, no stats


def _gru_transform_frozen(raw: np.ndarray, frozen: dict) -> np.ndarray:
    """ffill -> fill with FROZEN train mean -> clip(FROZEN) -> z-score(FROZEN μ/σ)."""
    c = normalize.clip(missing.fill_mean(missing.ffill(raw), frozen["fill_mean"]),
                       frozen["clip_lo"], frozen["clip_hi"])
    return normalize.normalize(c, frozen["mu"], frozen["sigma"])


@dataclass
class ScoreResult:
    prauc: float
    utility: float
    prauc_unmasked: float = float("nan")  # GRU only


def score_tree_frozen(booster, model_name: str, best_iter: int, tau: float,
                      b_data) -> ScoreResult:
    """Score setB with a frozen tree at frozen τ. One batched predict, split per patient."""
    summaries = [_tree_summary(raw) for raw, _ in b_data]
    lengths = [s.shape[0] for s in summaries]
    p_all = tree.booster_predict(booster, model_name, np.concatenate(summaries), best_iter)
    per_probs = np.split(p_all, np.cumsum(lengths)[:-1])
    per_labels = [lab for _, lab in b_data]
    prauc = float(average_precision_score(np.concatenate(per_labels), p_all))
    util = threshold.utility_at(per_labels, per_probs, tau)
    return ScoreResult(prauc=prauc, utility=util)


def score_gru_frozen(model: gru.GRUm2m, frozen: dict, tau: float, featureset: str,
                     b_data, batch_size: int = 64) -> ScoreResult:
    """Score setB with a frozen GRU at frozen τ. masked PR-AUC (padding excluded)."""
    transformed = [(_gru_transform_frozen(raw, frozen), lab.astype(np.float32))
                   for raw, lab in b_data]
    per_labels, per_probs, masked_prauc, _ = gru.evaluate(model, transformed, batch_size)
    util = threshold.utility_at(per_labels, per_probs, tau)
    # unmasked PR-AUC (padding label-0 included) — to confirm masking actually excludes padding
    ys, ps = [], []
    for i in range(0, len(transformed), batch_size):
        chunk = transformed[i:i + batch_size]
        X, Y, _, _ = sequence.collate_m2m(chunk)
        with torch.no_grad():
            p = torch.sigmoid(model(torch.from_numpy(X))).numpy()
        ys.append(Y.reshape(-1)); ps.append(p.reshape(-1))
    y, p = np.concatenate(ys), np.concatenate(ps)
    unmasked = float(average_precision_score(y, p)) if y.max() > 0 else float("nan")
    return ScoreResult(prauc=masked_prauc, utility=util, prauc_unmasked=unmasked)
