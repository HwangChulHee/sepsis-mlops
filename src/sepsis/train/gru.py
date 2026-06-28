"""H2-c — GRU many-to-many (full-scale promotion of scripts/smoke_m2m.py) (결정 5·6).

Unidirectional (causal) GRU, per-timestep logits. per-timestep BCE + pos_weight with
LOSS MASKING (padding excluded from loss AND eval). Early stopping on A-val loss;
model/HP SELECTION on A-val utility (separate). Right padding + validity mask come
from sequence.collate_m2m (H1). bidirectional is bound False via config.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

import numpy as np
import torch
from torch import nn

from sepsis import config as C
from sepsis.data import sequence

# sklearn is imported lazily inside evaluate() so the serving image (which only needs
# GRUm2m / forward_state) does not require scikit-learn.


class GRUm2m(nn.Module):
    def __init__(self, input_dim: int, hidden: int = 64, layers: int = 1, dropout: float = 0.0):
        super().__init__()
        self.gru = nn.GRU(input_dim, hidden, num_layers=layers, batch_first=True,
                          bidirectional=C.GRU_BIDIRECTIONAL,          # False -> causal
                          dropout=dropout if layers > 1 else 0.0)
        self.drop = nn.Dropout(dropout)
        self.head = nn.Linear(hidden, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # (B,T,F) -> (B,T)
        out, _ = self.gru(x)
        return self.head(self.drop(out)).squeeze(-1)

    def forward_state(self, x: torch.Tensor, h: torch.Tensor | None = None):
        """Stateful forward for streaming (H4 serving). x:(B,T,F), h:(num_layers,B,hidden)
        or None (zeros). Returns (logits (B,T), h_n). Causal: carrying h across calls is
        numerically identical to re-feeding 1..t (unidirectional GRU). Use .eval()."""
        out, h_n = self.gru(x, h)
        return self.head(self.drop(out)).squeeze(-1), h_n


def _train_epoch(model, data, loss_fn, opt, batch_size, seed, prog, ep, max_ep, nb):
    model.train()
    losses = []
    order = np.arange(len(data))
    np.random.default_rng(seed + ep).shuffle(order)
    for bi, i in enumerate(range(0, len(order), batch_size), 1):
        chunk = [data[j] for j in order[i:i + batch_size]]
        X, Y, V, _ = sequence.collate_m2m(chunk)
        logits = model(torch.from_numpy(X))
        le = loss_fn(logits, torch.from_numpy(Y))           # (B,T) reduction='none'
        Vt = torch.from_numpy(V).float()
        loss = (le * Vt).sum() / Vt.sum()                    # mask padding out of the loss
        opt.zero_grad()
        loss.backward()
        opt.step()
        losses.append(loss.item())
        if prog is not None and bi % 20 == 0:
            prog.update((ep - 1) * nb + bi,
                        f"epoch {ep}/{max_ep} | batch {bi}/{nb} | loss={np.mean(losses):.4f}")
    return float(np.mean(losses))


@torch.no_grad()
def evaluate(model, data, batch_size, loss_fn=None):
    """Per-patient (labels, probs) + flat masked PR-AUC + masked val loss.

    Padding is excluded by slicing each sequence to its real length (validity mask).
    """
    from sklearn.metrics import average_precision_score
    model.eval()
    per_labels, per_probs, ys, ps, vlosses = [], [], [], [], []
    for i in range(0, len(data), batch_size):
        chunk = data[i:i + batch_size]
        X, Y, V, lengths = sequence.collate_m2m(chunk)
        logits = model(torch.from_numpy(X))
        if loss_fn is not None:
            Vt = torch.from_numpy(V).float()
            le = loss_fn(logits, torch.from_numpy(Y))
            vlosses.append(((le * Vt).sum() / Vt.sum()).item())
        probs = torch.sigmoid(logits).numpy()
        for j, L in enumerate(lengths):
            lab = data[i + j][1]                # real per-patient labels (== Y[j,:L])
            pr = probs[j, :int(L)]              # padding excluded
            per_labels.append(lab)
            per_probs.append(pr)
            ys.append(lab)
            ps.append(pr)
    y, p = np.concatenate(ys), np.concatenate(ps)
    prauc = float(average_precision_score(y, p)) if y.max() > 0 else float("nan")
    val_loss = float(np.mean(vlosses)) if vlosses else float("nan")
    return per_labels, per_probs, prauc, val_loss


@dataclass
class GRUResult:
    model: GRUm2m
    best_val_loss: float
    n_epochs: int
    history: list = field(default_factory=list)
    masked_prauc: float = float("nan")
    unmasked_prauc: float = float("nan")


def train_gru(train_data, val_data, input_dim, hp, *, pos_weight, seed,
              max_epochs=25, patience=4, batch_size=64, prog=None) -> GRUResult:
    """Train m2m GRU with masked BCE; early-stop on A-val loss; restore best weights."""
    torch.manual_seed(seed)
    np.random.seed(seed)
    model = GRUm2m(input_dim, hp["hidden"], hp["layers"], hp["dropout"])
    loss_fn = nn.BCEWithLogitsLoss(
        pos_weight=torch.tensor([pos_weight], dtype=torch.float32), reduction="none")
    opt = torch.optim.Adam(model.parameters(), lr=hp["lr"])
    nb = (len(train_data) + batch_size - 1) // batch_size

    best_loss, best_state, bad, history = float("inf"), None, 0, []
    used = 0
    for ep in range(1, max_epochs + 1):
        tl = _train_epoch(model, train_data, loss_fn, opt, batch_size, seed, prog, ep, max_epochs, nb)
        _, _, _, vl = evaluate(model, val_data, batch_size, loss_fn=loss_fn)
        history.append({"epoch": ep, "train_loss": tl, "val_loss": vl})
        used = ep
        if prog is not None:
            prog.log(f"epoch {ep}/{max_epochs} train_loss={tl:.4f} val_loss={vl:.4f}")
        if vl < best_loss - 1e-5:
            best_loss, best_state, bad = vl, copy.deepcopy(model.state_dict()), 0
        else:
            bad += 1
            if bad >= patience:
                break
    if best_state is not None:
        model.load_state_dict(best_state)
    return GRUResult(model=model, best_val_loss=best_loss, n_epochs=used, history=history)
