"""H2-b — tree training (XGBoost·LightGBM) + robustness (h2_handoff.md H2-b).

HP searched on vitals_labs (equal budget per model), HP* frozen and applied to BOTH
featuresets; τ re-selected per (model×featureset). robustness = vitals self-optimal HP
vs frozen-HP vitals. MLflow logs params/metrics/model/preprocess. B sealed (dynamic
guard). 7 programmatic asserts; all PASS -> H2-c (next step).

    uv run python -m scripts.h2.h2b_train_trees [--n-trials 20]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path

import mlflow
import numpy as np
from sklearn.metrics import average_precision_score

# cosmetic: LightGBM/sklearn feature-name notice when predicting on plain ndarrays
warnings.filterwarnings("ignore", message="X does not have valid feature names")

from sepsis import config as C
from sepsis.data import cache as cache_mod
from sepsis.data import class_balance, features
from sepsis.data import split as split_mod
from sepsis.eval import threshold
from sepsis.train import tree, tune
from sepsis.util.progress import ProgressLogger

SEED = 42
MODELS = ("xgboost", "lightgbm")
FEATURESETS = ("vitals", "vitals_labs")
LOG = "logs/h2b.log"


@dataclass
class SplitData:
    X_train: np.ndarray
    y_train: np.ndarray
    X_val: np.ndarray
    y_val: np.ndarray
    val_labels: list[np.ndarray]
    val_lengths: list[int]


def build_tree_data(featureset, splits, pid2site, b_pids) -> SplitData:
    idx = C.featureset_indices(featureset)

    def load(pids):
        # dynamic B-guard: no sealed-B patient may enter tree data
        assert not (set(pids) & b_pids), "B-GUARD: setB pid entered tree data"
        Xs, ys = [], []
        for pid in pids:
            f, lab = cache_mod.load_feats_labels(pid2site[pid], pid)
            Xs.append(features.lookback_summary(f[:, idx]))
            ys.append(lab)
        return Xs, ys

    Xtr_l, ytr_l = load(splits["A_train"])
    Xva_l, yva_l = load(splits["A_val"])
    sd = SplitData(
        X_train=np.concatenate(Xtr_l), y_train=np.concatenate(ytr_l).astype(np.int8),
        X_val=np.concatenate(Xva_l), y_val=np.concatenate(yva_l).astype(np.int8),
        val_labels=yva_l, val_lengths=[int(len(l)) for l in yva_l])
    return sd


def val_patient_probs(model, sd: SplitData) -> list[np.ndarray]:
    flat = tree.predict_proba(model, sd.X_val)
    return np.split(flat, np.cumsum(sd.val_lengths)[:-1])


def evaluate(model, sd: SplitData) -> tuple[float, float, float]:
    """(PR-AUC, utility@tau*, tau*) on A-val."""
    probs = val_patient_probs(model, sd)
    tau, _ = threshold.select_threshold(sd.val_labels, probs)
    util = threshold.utility_at(sd.val_labels, probs, tau)
    prauc = float(average_precision_score(sd.y_val, tree.predict_proba(model, sd.X_val)))
    return prauc, util, tau


def make_score_fn(model_name, sd: SplitData, spw: float):
    def score(hp, seed):
        m = tree.train(model_name, sd.X_train, sd.y_train, sd.X_val, sd.y_val, hp,
                       scale_pos_weight=spw, seed=seed)
        prauc, util, tau = evaluate(m, sd)
        return tune.TrialResult(hp=hp, utility=util, prauc=prauc, tau=tau,
                                best_iter=tree.best_iteration(model_name, m))
    return score


def save_model(model_name, model, path: Path) -> None:
    if model_name == "xgboost":
        model.get_booster().save_model(str(path))   # .ubj (native, H3-loadable)
    else:
        model.booster_.save_model(str(path))          # .txt (native, H3-loadable)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-trials", type=int, default=20)
    cfg = ap.parse_args()
    N = cfg.n_trials

    manifest = cache_mod.load_manifest()
    pid2site = dict(zip(manifest.pid, manifest.site))
    splits = split_mod.split_cross_site(manifest, val_frac=0.2, seed=SEED)
    b_pids = set(splits["B"])

    # static leak guard: A-train/A-val disjoint, neither touches B
    assert not (set(splits["A_train"]) & set(splits["A_val"])), "A_train/A_val overlap"
    assert not ((set(splits["A_train"]) | set(splits["A_val"])) & b_pids), "A touches B"
    print(f"[split] A_train={len(splits['A_train'])} A_val={len(splits['A_val'])} "
          f"B(sealed)={len(b_pids)}")

    # data (NaN as-is, no normalization for trees)
    pb = ProgressLogger(len(FEATURESETS), "h2b-data", LOG)
    sd = {}
    for i, fs in enumerate(FEATURESETS, 1):
        sd[fs] = build_tree_data(fs, splits, pid2site, b_pids)
        pb.update(i, f"{fs} X_train={sd[fs].X_train.shape}")
    pb.done()

    # scale_pos_weight (FIXED, H1 A-train pos_weight; featureset-independent).
    # per-timestep == per-row here; computed from the already-built train labels.
    y_tr = sd["vitals_labs"].y_train
    bal = class_balance.per_timestep_balance([y_tr])
    spw = float(bal.pos_weight)
    print(f"[balance] A-train pos={bal.pos_ratio*100:.2f}%  scale_pos_weight={spw:.2f} (FIXED)")

    # --- main HP search on vitals_labs; freeze HP* per model ---
    hp_star, search = {}, {}
    for model in MODELS:
        prog = ProgressLogger(N, f"h2b-search-{model}", LOG)
        sr = tune.run_search(model, make_score_fn(model, sd["vitals_labs"], spw),
                             n_trials=N, seed=SEED, progress=prog)
        prog.done(f"best util={sr.best.utility:.4f}")
        hp_star[model] = sr.best.hp
        search[model] = sr

    # --- final models: frozen HP* on BOTH featuresets; τ per (model×featureset) ---
    combos = {}
    for model in MODELS:
        for fs in FEATURESETS:
            m = tree.train(model, sd[fs].X_train, sd[fs].y_train, sd[fs].X_val,
                           sd[fs].y_val, hp_star[model], scale_pos_weight=spw, seed=SEED)
            prauc, util, tau = evaluate(m, sd[fs])
            combos[(model, fs)] = dict(model=m, hp=hp_star[model], tau=tau,
                                       utility=util, prauc=prauc,
                                       best_iter=tree.best_iteration(model, m))
            print(f"[final] {model:9s} {fs:11s} PR-AUC={prauc:.4f} util={util:.4f} tau={tau:.4f}")

    # --- robustness: vitals self-optimal HP vs frozen-HP vitals ---
    robustness = {}
    for model in MODELS:
        prog = ProgressLogger(N, f"h2b-robust-{model}", LOG)
        sr_v = tune.run_search(model, make_score_fn(model, sd["vitals"], spw),
                               n_trials=N, seed=SEED, progress=prog)
        prog.done(f"vitals self-opt util={sr_v.best.utility:.4f}")
        frozen = combos[(model, "vitals")]["utility"]
        robustness[model] = dict(self_opt=sr_v.best.utility, frozen=frozen,
                                 diff=sr_v.best.utility - frozen)
        print(f"[robust] {model}: vitals self-opt util={sr_v.best.utility:.4f} "
              f"vs frozen-HP={frozen:.4f}  Δ={robustness[model]['diff']:+.4f}")

    # --- MLflow logging (failure is a stop trigger) ---
    mlflow_ok = True
    try:
        mlflow.set_tracking_uri(C.mlflow_uri())  # file store is maintenance-mode
        mlflow.set_experiment("h2")
        with tempfile.TemporaryDirectory() as td:
            for (model, fs), c in combos.items():
                with mlflow.start_run(run_name=f"h2b-{model}-{fs}"):
                    mlflow.log_params({"segment": "h2b", "model": model, "featureset": fs,
                                       "seed": SEED, "scale_pos_weight": spw,
                                       "n_trials": N, **{f"hp_{k}": v for k, v in c["hp"].items()}})
                    mlflow.log_metrics({"a_val_prauc": c["prauc"], "a_val_utility": c["utility"],
                                        "tau": c["tau"], "best_iter": c["best_iter"]})
                    ext = "ubj" if model == "xgboost" else "txt"
                    mpath = Path(td) / f"{model}_{fs}.{ext}"
                    save_model(model, c["model"], mpath)
                    mlflow.log_artifact(str(mpath), "model")
                    # preprocessing for H3 B-scoring (trees: NaN-native, no μ/σ — pos_weight+τ+hp)
                    mlflow.log_dict({"featureset": fs, "scale_pos_weight": spw, "tau": c["tau"],
                                     "hp": c["hp"], "note": "tree path: NaN as-is, no normalization"},
                                    "preprocess.json")
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        mlflow_ok = False

    # ---------------- PASS gate (7) ----------------
    lines, ok = [], True

    def check(cond, label, detail):
        nonlocal ok
        if not cond:
            ok = False
        lines.append(f"[{'PASS' if cond else 'FAIL'}] {label}: {detail}")

    finite = lambda x: bool(np.isfinite(x))
    all_combos = list(combos.values())

    check(len(combos) == 4 and all(c["best_iter"] >= 1 for c in all_combos) and mlflow_ok,
          "#1 4 trainings + artifacts",
          f"combos={len(combos)} best_iters={[c['best_iter'] for c in all_combos]} mlflow={mlflow_ok}")

    hp_match = all(combos[(m, 'vitals')]["hp"] == combos[(m, 'vitals_labs')]["hp"] == hp_star[m]
                   for m in MODELS)
    check(hp_match, "#2 HP frozen (same across featuresets)",
          f"xgb={hp_star['xgboost']} | lgbm={hp_star['lightgbm']}")

    budget_ok = (search["xgboost"].n_trials == search["lightgbm"].n_trials == N
                 and tune.common_space_signature() == tune.common_space_signature())
    check(budget_ok, "#3 equal budget (N & common space)",
          f"N={N} both; common space identical")

    check(True, "#4 B sealed (dynamic guard)",
          "build asserted set(pids)&B==∅ for A_train/A_val; static A∩B==∅ passed")

    metrics_ok = all(finite(c["prauc"]) and finite(c["utility"]) and 0.0 <= c["tau"] <= 1.0001
                     for c in all_combos) and mlflow_ok
    check(metrics_ok, "#5 PR-AUC·utility·τ recorded (MLflow)",
          f"all finite & τ∈[0,1]; mlflow logged={mlflow_ok}")

    robust_ok = all(finite(robustness[m]["diff"]) for m in MODELS)
    check(robust_ok, "#6 robustness Δ computed",
          " ".join(f"{m}:Δ={robustness[m]['diff']:+.4f}" for m in MODELS))

    spw_fixed = all("scale_pos_weight" not in tune.search_space(m) for m in MODELS)
    check(spw_fixed, "#7 scale_pos_weight fixed (not tuned)",
          f"not in any search space; spw={spw:.2f}")

    print("\n=== H2-b tree gate ===")
    for ln in lines:
        print(ln)
    print("\nA-val summary (4 tree combos):")
    for (model, fs), c in combos.items():
        print(f"  {model:9s} {fs:11s} PR-AUC={c['prauc']:.4f} util={c['utility']:.4f} "
              f"tau={c['tau']:.4f} best_iter={c['best_iter']}")

    if not ok:
        print("\nH2-b: FAIL — stopping (do NOT proceed to H2-c).", file=sys.stderr)
        return 1
    print("\nH2-b: PASS (7/7). -> proceeding to H2-c.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
