"""H2-c — GRU m2m full-scale training (h2_handoff.md H2-c).

Promotes the smoke_m2m wiring to full scale: unidirectional GRU, masked per-timestep
BCE, masked PR-AUC. HP searched on vitals_labs (utility objective, early-stop on
A-val loss), HP* frozen and applied to BOTH featuresets; τ re-selected per featureset.
MLflow (sqlite) logs params/metrics/state_dict + preprocessing (μ/σ·fill·clip·pos_weight·τ).
6 programmatic asserts.

    uv run python -m scripts.h2c_train_gru [--n-trials 6] [--max-epochs 25]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import mlflow
import numpy as np
import torch

from sepsis import config as C
from sepsis.data import cache as cache_mod
from sepsis.data import class_balance, missing, normalize
from sepsis.data import split as split_mod
from sepsis.eval import threshold
from sepsis.train import gru, tune
from sepsis.util.progress import ProgressLogger

SEED = 42
FEATURESETS = ("vitals", "vitals_labs")
LOG = "logs/h2c.log"


@dataclass
class GRUData:
    train: list          # [(feats T×F float32, labels T float32)]
    val: list
    val_labels: list     # per-patient label arrays
    input_dim: int
    stats: dict          # mu, sigma, fill_mean, clip_lo, clip_hi (A-train only)


def build_gru_data(featureset, splits, pid2site, b_pids) -> GRUData:
    idx = C.featureset_indices(featureset)
    lo, hi = normalize.clip_bounds(featureset)

    def load_raw(pids):
        assert not (set(pids) & b_pids), "B-GUARD: setB pid entered GRU data"
        out = {}
        for pid in pids:
            f, lab = cache_mod.load_feats_labels(pid2site[pid], pid)
            out[pid] = (f[:, idx].astype(np.float32), lab.astype(np.float32))
        return out

    tr_raw = load_raw(splits["A_train"])
    va_raw = load_raw(splits["A_val"])

    # train-only stats: fill_mean (post-ffill), then μ/σ (post fill+clip)
    fill_mean = missing.compute_fill_mean([missing.ffill(f) for f, _ in tr_raw.values()])
    mu, sigma = normalize.compute_norm_stats(
        [normalize.clip(missing.fill_mean(missing.ffill(f), fill_mean), lo, hi)
         for f, _ in tr_raw.values()])

    def transform(f):
        c = normalize.clip(missing.fill_mean(missing.ffill(f), fill_mean), lo, hi)
        return normalize.normalize(c, mu, sigma)

    train = [(transform(f), lab) for f, lab in tr_raw.values()]
    val = [(transform(f), lab) for f, lab in va_raw.values()]
    val_labels = [lab for _, lab in va_raw.values()]
    stats = {"mu": mu, "sigma": sigma, "fill_mean": fill_mean,
             "clip_lo": lo, "clip_hi": hi}
    return GRUData(train=train, val=val, val_labels=val_labels,
                   input_dim=len(idx), stats=stats)


def eval_combo(model, data: GRUData, batch_size):
    """(per-patient labels/probs, masked PR-AUC, unmasked PR-AUC)."""
    from sklearn.metrics import average_precision_score
    per_labels, per_probs, masked_prauc, _ = gru.evaluate(model, data.val, batch_size)
    # unmasked PR-AUC: pad to batch-max with label 0 included (smoke parity check)
    from sepsis.data import sequence
    ys, ps = [], []
    for i in range(0, len(data.val), batch_size):
        chunk = data.val[i:i + batch_size]
        X, Y, V, _ = sequence.collate_m2m(chunk)
        with torch.no_grad():
            p = torch.sigmoid(model(torch.from_numpy(X))).numpy()
        ys.append(Y.reshape(-1))
        ps.append(p.reshape(-1))
    y, p = np.concatenate(ys), np.concatenate(ps)
    unmasked = float(average_precision_score(y, p)) if y.max() > 0 else float("nan")
    return per_labels, per_probs, masked_prauc, unmasked


def make_score_fn(data: GRUData, spw, max_epochs, patience, batch_size):
    def score(hp, seed):
        nb = (len(data.train) + batch_size - 1) // batch_size
        prog = ProgressLogger(max_epochs * nb, f"h2c-trial-h{hp['hidden']}l{hp['layers']}", LOG)
        res = gru.train_gru(data.train, data.val, data.input_dim, hp, pos_weight=spw,
                            seed=seed, max_epochs=max_epochs, patience=patience,
                            batch_size=batch_size, prog=prog)
        per_labels, per_probs, masked_prauc, _ = gru.evaluate(res.model, data.val, batch_size)
        tau, util = threshold.select_threshold(per_labels, per_probs)
        prog.done(f"util={util:.4f} prauc={masked_prauc:.4f} epochs={res.n_epochs}")
        return tune.TrialResult(hp=hp, utility=util, prauc=masked_prauc, tau=tau,
                                best_iter=res.n_epochs)
    return score


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-trials", type=int, default=6)
    ap.add_argument("--max-epochs", type=int, default=25)
    ap.add_argument("--patience", type=int, default=4)
    ap.add_argument("--batch-size", type=int, default=64)
    cfg = ap.parse_args()

    torch.manual_seed(SEED)
    np.random.seed(SEED)

    manifest = cache_mod.load_manifest()
    pid2site = dict(zip(manifest.pid, manifest.site))
    splits = split_mod.split_cross_site(manifest, val_frac=0.2, seed=SEED)
    b_pids = set(splits["B"])
    assert not (set(splits["A_train"]) & set(splits["A_val"])), "A_train/A_val overlap"
    assert not ((set(splits["A_train"]) | set(splits["A_val"])) & b_pids), "A touches B"
    print(f"[split] A_train={len(splits['A_train'])} A_val={len(splits['A_val'])} "
          f"B(sealed)={len(b_pids)}")

    pb = ProgressLogger(len(FEATURESETS), "h2c-data", LOG)
    data = {}
    for i, fs in enumerate(FEATURESETS, 1):
        data[fs] = build_gru_data(fs, splits, pid2site, b_pids)
        pb.update(i, f"{fs} train={len(data[fs].train)} dim={data[fs].input_dim}")
    pb.done()

    spw = float(class_balance.per_timestep_balance(
        [lab.astype(np.int8) for _, lab in data["vitals_labs"].train]).pos_weight)
    print(f"[balance] scale/pos_weight={spw:.2f} (A-train, FIXED)")

    # --- HP search on vitals_labs; freeze HP* (selection = utility) ---
    tprog = ProgressLogger(cfg.n_trials, "h2c-search", LOG)
    sr = tune.run_search("gru", make_score_fn(data["vitals_labs"], spw, cfg.max_epochs,
                                              cfg.patience, cfg.batch_size),
                         n_trials=cfg.n_trials, seed=SEED, progress=tprog)
    tprog.done(f"best util={sr.best.utility:.4f} hp={sr.best.hp}")
    hp_star = sr.best.hp

    # --- final: frozen HP* on BOTH featuresets; τ per featureset ---
    combos = {}
    for fs in FEATURESETS:
        nb = (len(data[fs].train) + cfg.batch_size - 1) // cfg.batch_size
        prog = ProgressLogger(cfg.max_epochs * nb, f"h2c-final-{fs}", LOG)
        res = gru.train_gru(data[fs].train, data[fs].val, data[fs].input_dim, hp_star,
                            pos_weight=spw, seed=SEED, max_epochs=cfg.max_epochs,
                            patience=cfg.patience, batch_size=cfg.batch_size, prog=prog)
        per_labels, per_probs, masked, unmasked = eval_combo(res.model, data[fs], cfg.batch_size)
        tau, util = threshold.select_threshold(per_labels, per_probs)
        combos[fs] = dict(result=res, hp=hp_star, tau=tau, utility=util,
                          masked_prauc=masked, unmasked_prauc=unmasked,
                          val_loss=res.best_val_loss, epochs=res.n_epochs)
        prog.done(f"PR-AUC(masked)={masked:.4f} util={util:.4f} tau={tau:.4f}")
        print(f"[final] gru {fs:11s} PR-AUC(masked)={masked:.4f} (unmasked={unmasked:.4f}) "
              f"util={util:.4f} tau={tau:.4f} val_loss={res.best_val_loss:.4f} epochs={res.n_epochs}")

    # --- MLflow (sqlite); failure is a stop trigger ---
    mlflow_ok = True
    try:
        mlflow.set_tracking_uri(f"sqlite:///{C.ROOT}/mlflow.db")
        mlflow.set_experiment("h2")
        with tempfile.TemporaryDirectory() as td:
            for fs, c in combos.items():
                with mlflow.start_run(run_name=f"h2c-gru-{fs}"):
                    mlflow.log_params({"segment": "h2c", "model": "gru", "featureset": fs,
                                       "seed": SEED, "pos_weight": spw,
                                       "n_trials": cfg.n_trials, "max_epochs": cfg.max_epochs,
                                       **{f"hp_{k}": v for k, v in c["hp"].items()}})
                    mlflow.log_metrics({"a_val_prauc": c["masked_prauc"],
                                        "a_val_prauc_unmasked": c["unmasked_prauc"],
                                        "a_val_utility": c["utility"], "tau": c["tau"],
                                        "val_loss": c["val_loss"], "epochs": c["epochs"]})
                    sp = Path(td) / f"gru_{fs}.pt"
                    torch.save(c["result"].model.state_dict(), sp)
                    mlflow.log_artifact(str(sp), "model")
                    st = data[fs].stats
                    np.savez(Path(td) / f"pre_{fs}.npz", mu=st["mu"], sigma=st["sigma"],
                             fill_mean=st["fill_mean"], clip_lo=st["clip_lo"], clip_hi=st["clip_hi"])
                    mlflow.log_artifact(str(Path(td) / f"pre_{fs}.npz"), "preprocess")
                    mlflow.log_dict({"featureset": fs, "pos_weight": spw, "tau": c["tau"],
                                     "hp": c["hp"], "input_dim": data[fs].input_dim,
                                     "note": "GRU: ffill->train mean->clip->train z-score"},
                                    "preprocess.json")
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        mlflow_ok = False

    # ---------------- PASS gate (6) ----------------
    lines, ok = [], True

    def check(cond, label, detail):
        nonlocal ok
        if not cond:
            ok = False
        lines.append(f"[{'PASS' if cond else 'FAIL'}] {label}: {detail}")

    fin = lambda x: bool(np.isfinite(x))
    cv = combos["vitals"]
    cl = combos["vitals_labs"]

    check(len(combos) == 2 and mlflow_ok, "#1 2 trainings + artifacts",
          f"combos={len(combos)} mlflow={mlflow_ok}")
    check(C.GRU_BIDIRECTIONAL is False, "#2 unidirectional + right padding",
          f"bidirectional={C.GRU_BIDIRECTIONAL}")
    masked_diff = all(fin(c["masked_prauc"]) and fin(c["unmasked_prauc"])
                      and abs(c["masked_prauc"] - c["unmasked_prauc"]) > 1e-9 for c in combos.values())
    check(masked_diff, "#3 padding excluded (masked != unmasked)",
          " ".join(f"{fs}:m={c['masked_prauc']:.4f}/u={c['unmasked_prauc']:.4f}"
                   for fs, c in combos.items()))
    check(cv["hp"] == cl["hp"] == hp_star, "#4 HP frozen + selection=utility",
          f"hp*={hp_star}")
    metrics_ok = all(fin(c["masked_prauc"]) and fin(c["utility"]) and 0 <= c["tau"] <= 1.0001
                     for c in combos.values()) and mlflow_ok
    check(metrics_ok, "#5 PR-AUC·utility·τ recorded", f"mlflow={mlflow_ok}")
    loss_ok = all(fin(c["val_loss"]) for c in combos.values())
    check(loss_ok, "#6 A-val loss finite",
          " ".join(f"{fs}:{c['val_loss']:.4f}" for fs, c in combos.items()))

    print("\n=== H2-c GRU gate ===")
    for ln in lines:
        print(ln)
    print("\nA-val summary (2 GRU combos):")
    for fs, c in combos.items():
        print(f"  gru {fs:11s} PR-AUC(masked)={c['masked_prauc']:.4f} util={c['utility']:.4f} "
              f"tau={c['tau']:.4f} epochs={c['epochs']}")

    if not ok:
        print("\nH2-c: FAIL — stopping.", file=sys.stderr)
        return 1
    print("\nH2-c: PASS (6/6). H2 training done (b+c). Next: H2-d (human checkpoint).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
