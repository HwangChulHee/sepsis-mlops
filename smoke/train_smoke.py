"""End-to-end smoke training: data -> windowing -> GRU -> eval -> MLflow.

This verifies PLUMBING, not performance. Every PASS criterion from Handoff 02 is
checked with an assert or an explicit print. Run:

    uv run python -m smoke.train_smoke
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

# Handoff sanctions a local mlruns/ file store; mlflow 3.x gates it behind this opt-out.
os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")

import mlflow
import mlflow.pytorch
import numpy as np
import torch
from sklearn.metrics import average_precision_score
from torch import nn
from torch.utils.data import DataLoader

from . import data as D
from .dataset import WINDOW, WindowDataset, make_windows
from .model import GRUClassifier

ROOT = Path(__file__).resolve().parent.parent


def set_seed(seed: int) -> None:
    np.random.seed(seed)
    torch.manual_seed(seed)


def build_loaders(cfg):
    # --- subset across both sites ---
    files = D.sample_subset(cfg.n_patients, seed=cfg.seed)
    sites = {s for s, _ in files}
    assert {"training_setA", "training_setB"} <= sites, f"both sites required, got {sites}"
    print(f"[subset] {len(files)} patients; sites present: {sorted(sites)}")

    patients = [D.load_patient(s, p) for s, p in files]

    # --- patient-level, site-aware split ---
    train_p, val_p = D.split_patients(patients, val_frac=cfg.val_frac, seed=cfg.seed)
    train_ids = {p.pid for p in train_p}
    val_ids = {p.pid for p in val_p}
    assert train_ids.isdisjoint(val_ids), "patient leakage: train/val overlap!"
    print(f"[split] train={len(train_p)} val={len(val_p)} patients; "
          f"intersection={len(train_ids & val_ids)} (must be 0)")
    for name, grp in (("train", train_p), ("val", val_p)):
        a = sum(p.site == "training_setA" for p in grp)
        b = sum(p.site == "training_setB" for p in grp)
        print(f"[split] {name}: A={a} B={b}")

    # --- normalization stats from TRAIN only ---
    mean, std = D.compute_train_stats(train_p)
    train_p = [D.normalize_patient(p, mean, std) for p in train_p]
    val_p = [D.normalize_patient(p, mean, std) for p in val_p]

    # --- windows ---
    Xtr, ytr = make_windows(train_p)
    Xva, yva = make_windows(val_p)
    print(f"[windows] train={Xtr.shape} val={Xva.shape}")
    assert Xtr.shape[0] > 0 and Xva.shape[0] > 0, "no windows generated"
    n_pos_tr = int(ytr.sum())
    n_pos_va = int(yva.sum())
    print(f"[windows] positive windows: train={n_pos_tr} val={n_pos_va}")
    assert n_pos_tr > 0, "no positive windows in train subset (increase n_patients/seed)"

    # --- NaN guard right before the model ---
    assert not np.isnan(Xtr).any(), "NaN in train features!"
    assert not np.isnan(Xva).any(), "NaN in val features!"
    print("[nan-check] 0 NaNs in train/val features -> OK")

    # --- pos_weight from TRAIN window balance ---
    n_neg_tr = Xtr.shape[0] - n_pos_tr
    pos_weight = n_neg_tr / max(n_pos_tr, 1)
    print(f"[pos_weight] neg={n_neg_tr} pos={n_pos_tr} -> pos_weight={pos_weight:.2f}")

    tr_loader = DataLoader(WindowDataset(Xtr, ytr), batch_size=cfg.batch_size, shuffle=True)
    va_loader = DataLoader(WindowDataset(Xva, yva), batch_size=cfg.batch_size, shuffle=False)
    meta = dict(
        n_windows_train=Xtr.shape[0], n_windows_val=Xva.shape[0],
        n_pos_train=n_pos_tr, n_pos_val=n_pos_va, pos_weight=pos_weight,
        n_features=Xtr.shape[2], sample_X=Xva[:1],
    )
    return tr_loader, va_loader, meta


def run_epoch(model, loader, loss_fn, optimizer=None):
    train = optimizer is not None
    model.train(train)
    losses, all_logits, all_y = [], [], []
    for xb, yb in loader:
        logits = model(xb)
        loss = loss_fn(logits, yb)
        if train:
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
        losses.append(loss.item())
        all_logits.append(logits.detach())
        all_y.append(yb.detach())
    avg_loss = float(np.mean(losses))
    probs = torch.sigmoid(torch.cat(all_logits)).numpy()
    ys = torch.cat(all_y).numpy()
    return avg_loss, probs, ys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-patients", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--val-frac", type=float, default=0.2)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--batch-size", type=int, default=256)
    ap.add_argument("--hidden", type=int, default=64)
    ap.add_argument("--lr", type=float, default=1e-3)
    cfg = ap.parse_args()

    set_seed(cfg.seed)
    torch.set_num_threads(max(1, torch.get_num_threads()))

    tr_loader, va_loader, meta = build_loaders(cfg)

    model = GRUClassifier(input_dim=meta["n_features"], hidden_dim=cfg.hidden, num_layers=1)
    pos_weight = torch.tensor([meta["pos_weight"]], dtype=torch.float32)
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    optimizer = torch.optim.Adam(model.parameters(), lr=cfg.lr)

    mlflow.set_tracking_uri(f"file:{ROOT / 'mlruns'}")
    mlflow.set_experiment("sepsis-smoke")
    with mlflow.start_run(run_name="smoke") as run:
        mlflow.log_params(dict(
            window=WINDOW, n_patients=cfg.n_patients, n_features=meta["n_features"],
            hidden=cfg.hidden, lr=cfg.lr, epochs=cfg.epochs, batch_size=cfg.batch_size,
            pos_weight=round(meta["pos_weight"], 4), seed=cfg.seed,
            n_windows_train=meta["n_windows_train"], n_windows_val=meta["n_windows_val"],
        ))

        for epoch in range(1, cfg.epochs + 1):
            tr_loss, _, _ = run_epoch(model, tr_loader, loss_fn, optimizer)
            va_loss, va_probs, va_y = run_epoch(model, va_loader, loss_fn)
            assert np.isfinite(tr_loss), f"train loss not finite: {tr_loss}"
            # PR-AUC needs both classes present in val; guard for the smoke
            if va_y.max() > 0 and va_y.min() < 1:
                va_prauc = float(average_precision_score(va_y, va_probs))
            else:
                va_prauc = float("nan")
            print(f"[epoch {epoch}] train_loss={tr_loss:.4f} val_loss={va_loss:.4f} "
                  f"val_pr_auc={va_prauc:.4f}")
            mlflow.log_metric("train_loss", tr_loss, step=epoch)
            mlflow.log_metric("val_loss", va_loss, step=epoch)
            mlflow.log_metric("val_pr_auc", va_prauc, step=epoch)

        # --- log model artifact ---
        # pickle (not the pt2 default): pt2 traces via torch.export, which fails on
        # GRU + batch-dim-1 input_example; pickle stores the model directly.
        sample = torch.from_numpy(meta["sample_X"])
        model.eval()
        mlflow.pytorch.log_model(
            model, name="model",
            serialization_format="pickle",
            input_example=meta["sample_X"],
        )

        # --- single-window inference sanity check ---
        model.eval()
        with torch.no_grad():
            prob = torch.sigmoid(model(sample)).item()
        assert 0.0 <= prob <= 1.0, f"inference prob out of range: {prob}"
        print(f"[infer] single-window probability = {prob:.4f} (in [0,1]) -> OK")
        mlflow.log_metric("single_window_prob", prob)

        print(f"[mlflow] run_id={run.info.run_id} logged to {ROOT / 'mlruns'}")
    print("SMOKE PASS")


if __name__ == "__main__":
    main()
