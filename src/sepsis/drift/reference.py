"""H4d-a — A-train RAW reference, per-patient-summary unit (DDD 결정 1, handoff H4d-a).

Reference = A-train input distribution at the SAME unit the drift test uses: one
observation per patient = the last observed value per feature (= the serving ffill
end-state). RAW values (NOT μ/σ normalization stats) and per-feature missing rate are
frozen. Per-patient (not per-timestep) avoids the autocorrelation that breaks the test.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from sepsis import config as C
from sepsis.data import cache as cache_mod, missing, split as split_mod

LOW_CARD_MAX_UNIQUE = 5   # n_unique <= this -> categorical (JS), else numeric (PSI/Wasserstein)
REF_DIR = C.ROOT / "data" / "drift"


def patient_last_summary(raw_slice: np.ndarray) -> np.ndarray:
    """One row per patient: last OBSERVED value per feature (NaN if never observed).

    = missing.ffill(raw)[-1] — exactly the serving ffill end-state (last carried obs).
    """
    return missing.ffill(raw_slice)[-1]


@dataclass
class Reference:
    featureset: str
    cols: list[str]
    unit: str                  # "patient_last" — MUST match current's unit
    summary: np.ndarray        # (n_patients, F) per-patient last-observed (raw, NaN-preserved)
    missing_rate: np.ndarray   # (F,) fraction of patients never-observing each feature
    low_card: np.ndarray       # (F,) bool — categorical features (JS) vs numeric (PSI)
    n_patients: int


def _low_card(summary: np.ndarray) -> np.ndarray:
    out = np.zeros(summary.shape[1], dtype=bool)
    for j in range(summary.shape[1]):
        v = summary[:, j]
        out[j] = len(np.unique(v[~np.isnan(v)])) <= LOW_CARD_MAX_UNIQUE
    return out


def build_reference(featureset: str = "vitals", *, val_frac: float = 0.2,
                    seed: int = 42) -> Reference:
    """Build the frozen reference from A-train (cross_site split)."""
    idx = C.featureset_indices(featureset)
    cols = C.featureset_columns(featureset)
    manifest = cache_mod.load_manifest()
    pid2site = dict(zip(manifest.pid, manifest.site))
    a_train = split_mod.split_cross_site(manifest, val_frac=val_frac, seed=seed)["A_train"]

    rows = []
    for pid in a_train:
        feats, _ = cache_mod.load_feats_labels(pid2site[pid], pid)
        rows.append(patient_last_summary(feats[:, idx].astype(np.float32)))
    summary = np.vstack(rows).astype(np.float32)           # (n_patients, F)
    missing_rate = np.isnan(summary).mean(axis=0).astype(np.float32)
    return Reference(featureset=featureset, cols=cols, unit="patient_last",
                     summary=summary, missing_rate=missing_rate,
                     low_card=_low_card(summary), n_patients=summary.shape[0])


def save_reference(ref: Reference, path: Path | None = None) -> Path:
    path = Path(path) if path else REF_DIR / f"reference_{ref.featureset}.npz"
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, summary=ref.summary, missing_rate=ref.missing_rate,
             low_card=ref.low_card, cols=np.array(ref.cols), unit=ref.unit,
             featureset=ref.featureset, n_patients=ref.n_patients)
    return path


def load_reference(path: Path) -> Reference:
    z = np.load(path, allow_pickle=False)
    return Reference(featureset=str(z["featureset"]), cols=[str(c) for c in z["cols"]],
                     unit=str(z["unit"]), summary=z["summary"], missing_rate=z["missing_rate"],
                     low_card=z["low_card"], n_patients=int(z["n_patients"]))
