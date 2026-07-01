"""H2-d — aggregate 6-combo A-val results + select representative baseline (결정 1·7).

Reads ONLY A-val metrics from MLflow (model selection never touches sealed B). Picks
the representative tree baseline by A-val utility (utility-primary, PR-AUC secondary).
The MAIN featureset is intentionally NOT fixed here (deferred to H3 cross-site + H4).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import mlflow
import pandas as pd

TREE_MODELS = ("xgboost", "lightgbm")
MODEL_ORDER = {"gru": 0, "xgboost": 1, "lightgbm": 2}


def load_results(tracking_uri: str, experiment: str = "h2") -> pd.DataFrame:
    """6-combo A-val table from MLflow. Only a_val_* metrics are read (B never logged)."""
    mlflow.set_tracking_uri(tracking_uri)
    client = mlflow.tracking.MlflowClient()
    exp = client.get_experiment_by_name(experiment)
    if exp is None:
        raise RuntimeError(f"MLflow experiment {experiment!r} not found")
    runs = mlflow.search_runs(experiment_ids=[exp.experiment_id])

    # leak guard: model selection must use only A-val metrics
    metric_cols = [c for c in runs.columns if c.startswith("metrics.")]
    bad = [c for c in metric_cols if not c.startswith("metrics.a_val")
           and c not in ("metrics.tau", "metrics.val_loss", "metrics.epochs",
                         "metrics.best_iter")]
    if bad:
        raise RuntimeError(f"non-A-val metric present (possible B leak): {bad}")

    rows = []
    for _, r in runs.iterrows():
        rows.append({
            "model": r["params.model"],
            "featureset": r["params.featureset"],
            "prauc": float(r["metrics.a_val_prauc"]),
            "utility": float(r["metrics.a_val_utility"]),
            "tau": float(r["metrics.tau"]),
        })
    df = pd.DataFrame(rows)
    df["_m"] = df["model"].map(MODEL_ORDER)
    df = df.sort_values(["utility"], ascending=False).reset_index(drop=True)
    return df


def parse_robustness(log_path: str, frozen_utility: dict[str, float]) -> pd.DataFrame:
    """Per-model robustness: vitals self-optimal HP vs frozen-HP vitals (from H2-b log).

    frozen_utility[model] = the (model, vitals) A-val utility from MLflow.
    """
    pat = re.compile(r"\[h2b-robust-([a-z]+)\] done.*?vitals self-opt util=([0-9.]+)")
    self_opt: dict[str, float] = {}
    with open(log_path) as f:
        for line in f:
            m = pat.search(line)
            if m:
                self_opt[m.group(1)] = float(m.group(2))  # last occurrence wins
    rows = []
    for model in TREE_MODELS:
        so = self_opt.get(model, float("nan"))
        fr = frozen_utility.get(model, float("nan"))
        rows.append({"model": model, "vitals_self_opt": so, "frozen_hp_vitals": fr,
                     "delta": so - fr})
    return pd.DataFrame(rows)


@dataclass
class BaselineChoice:
    model: str
    by_featureset: dict[str, float]   # tree model -> {featureset: utility}
    dominates: bool                   # winner >= other on BOTH featuresets
    rationale: str


def select_baseline(df: pd.DataFrame) -> BaselineChoice:
    """Representative tree baseline = higher A-val utility (utility-primary). B unused."""
    util = {m: {} for m in TREE_MODELS}
    for _, r in df.iterrows():
        if r["model"] in TREE_MODELS:
            util[r["model"]][r["featureset"]] = r["utility"]
    missing = [m for m, fs in util.items() if not fs]
    if missing:   # 빈 dict 면 max() 가 불명확한 ValueError → 무엇이 없는지 명시적으로 실패
        raise ValueError(f"no rows for tree model(s) {missing} in df — "
                         f"select_baseline 은 각 TREE_MODELS 에 최소 1개 결과를 요구한다")
    best_per_model = {m: max(fs.values()) for m, fs in util.items()}
    winner = max(best_per_model, key=best_per_model.get)
    other = [m for m in TREE_MODELS if m != winner][0]
    dominates = all(util[winner].get(fs, -1) >= util[other].get(fs, 1e9)
                    for fs in set(util[winner]) | set(util[other]))
    rationale = (
        f"{winner} A-val utility {best_per_model[winner]:.4f} > "
        f"{other} {best_per_model[other]:.4f} (utility-primary; B sealed). "
        + ("Dominates on BOTH featuresets." if dominates else "Wins on best featureset.")
    )
    return BaselineChoice(model=winner, by_featureset=util, dominates=dominates,
                          rationale=rationale)
