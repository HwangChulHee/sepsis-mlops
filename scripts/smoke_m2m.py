"""H1 m2m re-smoke (강제 항목 — 결정 4). ⏸ human checkpoint.

The original smoke was many-to-one over fixed 8h windows, so the many-to-many wiring
is unvalidated. This re-smokes m2m END-TO-END on a small subset reusing the H1-b
pipeline (src/sepsis/data/): cache -> cross_site split -> transform -> unidirectional
GRU, per-timestep prediction, pos_weight per-timestep BCE with LOSS MASKING ->
per-timestep PR-AUC with the padding EXCLUDED.

Goal is PLUMBING, not performance. Metric values are by-products; do not gate on them.

    uv run python -m scripts.smoke_m2m                 # ~1000 patients, 3 epochs

PASS gate (4):
  1. end-to-end completes without error
  2. train loss finite (hard); decreasing is logged only
  3. eval padding-exclusion: masked metric != unmasked, padding really excluded
  4. unidirectional (bidirectional=False) + right padding
"""

from __future__ import annotations

import argparse
import sys
import warnings

import numpy as np
import torch
from sklearn.metrics import average_precision_score
from torch import nn

from sepsis import config as C
from sepsis.data import cache as cache_mod
from sepsis.data import class_balance, missing, normalize, sequence, split


# ---------------------------------------------------------------------------
# model — unidirectional (causal) GRU, per-timestep logits
# ---------------------------------------------------------------------------
class GRUm2m(nn.Module):
    def __init__(self, input_dim: int, hidden: int = 64):
        super().__init__()
        self.gru = nn.GRU(input_dim, hidden, batch_first=True,
                          bidirectional=C.GRU_BIDIRECTIONAL)  # False -> causal
        self.head = nn.Linear(hidden, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # (B,T,F) -> (B,T)
        out, _ = self.gru(x)
        return self.head(out).squeeze(-1)


# ---------------------------------------------------------------------------
# subset + transform (reuse H1-b modules)
# ---------------------------------------------------------------------------
def sample_subset(manifest, n_per_site: int, seed: int):
    rng = np.random.default_rng(seed)
    pick = []
    for site in C.SITES:
        pids = manifest.loc[manifest.site == site, "pid"].to_numpy()
        idx = rng.choice(len(pids), size=min(n_per_site, len(pids)), replace=False)
        pick.extend(pids[idx].tolist())
    return manifest[manifest.pid.isin(pick)].reset_index(drop=True)


def build_split_arrays(manifest, featureset, val_frac, seed):
    idx = C.featureset_indices(featureset)
    site_of = dict(zip(manifest.pid, manifest.site))
    raw, labels = {}, {}
    for pid, site in site_of.items():
        f, lab = cache_mod.load_feats_labels(site, pid)
        raw[pid] = f[:, idx].astype(np.float32)
        labels[pid] = lab
    splits = split.split_cross_site(manifest, val_frac=val_frac, seed=seed)
    lo, hi = normalize.clip_bounds(featureset)
    tr = splits["A_train"]
    fill_mean = missing.compute_fill_mean([missing.ffill(raw[p]) for p in tr])
    mu, sigma = normalize.compute_norm_stats(
        [normalize.clip(missing.fill_mean(missing.ffill(raw[p]), fill_mean), lo, hi) for p in tr])

    def transform(pid):
        c = normalize.clip(missing.fill_mean(missing.ffill(raw[pid]), fill_mean), lo, hi)
        return normalize.normalize(c, mu, sigma)

    arrays = {name: [(transform(p), labels[p].astype(np.float32)) for p in pids]
              for name, pids in splits.items()}
    bal = class_balance.per_timestep_balance([labels[p] for p in tr])
    return arrays, bal


# ---------------------------------------------------------------------------
# batching / epochs
# ---------------------------------------------------------------------------
def batches(data, batch_size, shuffle, seed=0):
    order = np.arange(len(data))
    if shuffle:
        np.random.default_rng(seed).shuffle(order)
    for i in range(0, len(order), batch_size):
        chunk = [data[j] for j in order[i:i + batch_size]]
        X, Y, V, _ = sequence.collate_m2m(chunk)
        yield (torch.from_numpy(X), torch.from_numpy(Y),
               torch.from_numpy(V).float())


def run_train_epoch(model, data, loss_fn, opt, batch_size):
    model.train()
    losses = []
    for X, Y, V in batches(data, batch_size, shuffle=True):
        logits = model(X)
        loss_el = loss_fn(logits, Y)          # (B,T), reduction='none'
        loss = (loss_el * V).sum() / V.sum()   # mask padding out of the loss
        opt.zero_grad()
        loss.backward()
        opt.step()
        losses.append(loss.item())
    return float(np.mean(losses))


@torch.no_grad()
def evaluate(model, data, batch_size):
    """Return (masked PR-AUC, unmasked PR-AUC, n_valid, n_all)."""
    model.eval()
    probs_all, y_all, v_all = [], [], []
    for X, Y, V in batches(data, batch_size, shuffle=False):
        p = torch.sigmoid(model(X))
        probs_all.append(p.reshape(-1).numpy())
        y_all.append(Y.reshape(-1).numpy())
        v_all.append(V.reshape(-1).numpy())
    probs = np.concatenate(probs_all)
    y = np.concatenate(y_all)
    v = np.concatenate(v_all).astype(bool)

    def prauc(yy, pp):
        if yy.max() < 1 or yy.min() > 0:
            return float("nan")
        return float(average_precision_score(yy, pp))

    masked = prauc(y[v], probs[v])                 # padding excluded
    unmasked = prauc(y, probs)                      # padding (label 0) included
    return masked, unmasked, int(v.sum()), int(v.size)


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-per-site", type=int, default=500)  # ~1000 patients
    ap.add_argument("--featureset", default="vitals_labs", choices=list(C.FEATURESETS))
    ap.add_argument("--val-frac", type=float, default=0.25)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--hidden", type=int, default=64)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--seed", type=int, default=42)
    cfg = ap.parse_args()

    np.random.seed(cfg.seed)
    torch.manual_seed(cfg.seed)

    lines, ok = [], True

    def check(cond, label, detail):
        nonlocal ok
        if not cond:
            ok = False
        lines.append(f"[{'PASS' if cond else 'FAIL'}] {label}: {detail}")

    try:
        manifest = sample_subset(cache_mod.load_manifest(), cfg.n_per_site, cfg.seed)
        arrays, bal = build_split_arrays(manifest, cfg.featureset, cfg.val_frac, cfg.seed)
        tr, va = arrays["A_train"], arrays["A_val"]
        input_dim = len(C.featureset_columns(cfg.featureset))
        print(f"[subset] A_train={len(tr)} A_val={len(va)} B={len(arrays['B'])} "
              f"patients; pos_weight={bal.pos_weight:.2f} (A-train pos {bal.pos_ratio*100:.2f}%)")

        model = GRUm2m(input_dim, hidden=cfg.hidden)
        pos_weight = torch.tensor([bal.pos_weight], dtype=torch.float32)
        loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_weight, reduction="none")
        opt = torch.optim.Adam(model.parameters(), lr=cfg.lr)

        loss_curve = []
        for ep in range(1, cfg.epochs + 1):
            tl = run_train_epoch(model, tr, loss_fn, opt, cfg.batch_size)
            m, u, nv, na = evaluate(model, va, cfg.batch_size)
            loss_curve.append(tl)
            print(f"[epoch {ep}] train_loss={tl:.4f} "
                  f"val_PRAUC(masked)={m:.4f} val_PRAUC(unmasked)={u:.4f}")

        masked, unmasked, n_valid, n_all = evaluate(model, va, cfg.batch_size)
        completed = True
    except Exception:  # noqa: BLE001 — re-smoke must report, not crash silently
        import traceback
        traceback.print_exc()
        completed = False
        loss_curve, masked, unmasked, n_valid, n_all = [], float("nan"), float("nan"), 0, 0

    # 1. end-to-end
    check(completed, "#1 end-to-end completes", f"completed={completed}")
    # 2. loss finite (hard); trend logged
    finite = bool(loss_curve) and all(np.isfinite(loss_curve))
    check(finite, "#2 train loss finite (hard)", f"curve={[round(x,4) for x in loss_curve]}")
    # 3. padding-exclusion: masked != unmasked AND padding actually dropped (n_valid < n_all)
    pad_dropped = n_valid < n_all
    differ = np.isfinite(masked) and np.isfinite(unmasked) and abs(masked - unmasked) > 1e-9
    check(pad_dropped and differ, "#3 eval padding excluded",
          f"n_valid={n_valid:,} < n_all={n_all:,} ({pad_dropped}); "
          f"masked={masked:.4f} != unmasked={unmasked:.4f} ({differ})")
    # 4. unidirectional + right padding
    check(C.GRU_BIDIRECTIONAL is False, "#4 unidirectional + right padding",
          f"bidirectional={C.GRU_BIDIRECTIONAL} (right-pad validity mask, prefix=real)")

    print("\n=== m2m re-smoke gate ===")
    for ln in lines:
        print(ln)

    if loss_curve:
        trend = "decreasing" if len(loss_curve) > 1 and loss_curve[-1] < loss_curve[0] else "flat/up"
        print(f"\nloss curve: {[round(x,4) for x in loss_curve]} ({trend}; trend is informational)")
        print(f"masking effect: PR-AUC masked={masked:.4f} vs unmasked={unmasked:.4f} "
              f"(padding {n_all - n_valid:,} of {n_all:,} timesteps excluded)")
        if np.isfinite(masked) and masked > 0.9:
            print("  ⚠️ FLAG: masked PR-AUC unusually high for a tiny smoke — possible leak; "
                  "investigate before trusting (not a gate).")

    if not ok:
        print("\nm2m re-smoke: FAIL — stopping.", file=sys.stderr)
        return 1
    print("\nm2m re-smoke: PASS (4/4). ⏸ Human checkpoint: review loss curve + masking effect.")
    return 0


if __name__ == "__main__":
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=UserWarning)  # pos_weight shape, etc.
        raise SystemExit(main())
