"""H2-b/c — hyperparameter search (random, equal budget per model) (결정 6).

HP↔τ nesting (handoff): each trial's SCORE = the A-val utility at its τ-maximizing
threshold; the best trial's HP is frozen. Fair budget = same trial count N and same
COMMON search space across models (the depth knob differs by library nature:
XGBoost max_depth vs LightGBM num_leaves).

Search ranges are [우리 결정] (default-informed; official docs give defaults only, not
ranges — handoff review). lr loguniform; subsample/colsample/reg_lambda uniform.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# common space (identical across models) + model-specific depth knob
_COMMON = {
    "learning_rate": ("loguniform", 0.01, 0.3),
    "subsample": ("uniform", 0.6, 1.0),
    "colsample_bytree": ("uniform", 0.6, 1.0),
    "reg_lambda": ("uniform", 0.0, 10.0),
}
_DEPTH = {
    "xgboost": {"max_depth": ("choice", [3, 5, 7])},
    "lightgbm": {"num_leaves": ("choice", [15, 31, 63])},
}

# GRU space (결정 6 / handoff H2-c) — separate from the tree common space
_GRU = {
    "hidden": ("choice", [32, 64, 128]),
    "layers": ("choice", [1, 2]),
    "lr": ("loguniform", 1e-4, 1e-2),
    "dropout": ("uniform", 0.0, 0.3),
}


def search_space(model_name: str) -> dict:
    if model_name == "gru":
        return dict(_GRU)
    return {**_COMMON, **_DEPTH[model_name]}


def common_space_signature() -> dict:
    """For the fair-budget assert: the COMMON space is identical across models."""
    return dict(_COMMON)


def _sample_one(spec, rng: np.random.Generator):
    kind = spec[0]
    if kind == "loguniform":
        lo, hi = spec[1], spec[2]
        return float(np.exp(rng.uniform(np.log(lo), np.log(hi))))
    if kind == "uniform":
        return float(rng.uniform(spec[1], spec[2]))
    if kind == "choice":
        return spec[1][int(rng.integers(len(spec[1])))]
    raise ValueError(f"unknown spec {spec!r}")


def sample_hp(model_name: str, rng: np.random.Generator) -> dict:
    return {k: _sample_one(spec, rng) for k, spec in search_space(model_name).items()}


@dataclass
class TrialResult:
    hp: dict
    utility: float
    prauc: float
    tau: float
    best_iter: int


@dataclass
class SearchResult:
    model_name: str
    n_trials: int
    best: TrialResult
    trials: list[TrialResult] = field(default_factory=list)


def run_search(model_name: str, score_fn, *, n_trials: int, seed: int,
               progress=None) -> SearchResult:
    """score_fn(hp, seed) -> TrialResult. Picks the trial with the highest utility.

    Each trial uses an independent rng (seed, trial) so runs are reproducible and the
    budget (n_trials) is identical across models.
    """
    base = np.random.default_rng(seed)
    trials: list[TrialResult] = []
    best: TrialResult | None = None
    for k in range(n_trials):
        rng = np.random.default_rng([seed, k])
        hp = sample_hp(model_name, rng)
        res = score_fn(hp, seed)
        trials.append(res)
        if best is None or res.utility > best.utility:
            best = res
        if progress is not None:
            progress.update(k + 1, f"{model_name} util={res.utility:.4f} "
                                   f"(best {best.utility:.4f})")
    return SearchResult(model_name=model_name, n_trials=n_trials, best=best, trials=trials)
