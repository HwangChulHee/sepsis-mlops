"""Subset selection + preprocessing for the smoke pipeline.

Design decisions are fixed by Handoff 02 (justified by the EDA in reports/eda_findings.md):
- 10 features: 8 vitals + Age + Gender (labs/Unit*/ICULOS excluded for the smoke).
- forward-fill within each patient (past->future only, no leakage), then fill any
  still-missing column with the TRAIN-split column mean. zero-fill is forbidden.
- z-score standardization using TRAIN-split mean/std only.
- patient-level, site-aware train/val split (file == patient, so grouping is trivial).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "raw"
SETS = ("training_setA", "training_setB")

VITALS = ["HR", "O2Sat", "Temp", "SBP", "MAP", "DBP", "Resp", "EtCO2"]
DEMOGRAPHICS = ["Age", "Gender"]
FEATURES = VITALS + DEMOGRAPHICS  # 10 features
LABEL = "SepsisLabel"


@dataclass
class Patient:
    pid: str
    site: str
    feats: np.ndarray  # (T, 10) float32, forward-filled (may still contain NaN)
    labels: np.ndarray  # (T,) int64


def list_patient_files() -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for s in SETS:
        for p in sorted((DATA_DIR / s).glob("*.psv")):
            files.append((s, p))
    return files


def sample_subset(n_patients: int, seed: int) -> list[tuple[str, Path]]:
    """Random subset across BOTH sites with a fixed seed."""
    files = list_patient_files()
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(files), size=min(n_patients, len(files)), replace=False)
    return [files[i] for i in sorted(idx)]


def load_patient(site: str, path: Path) -> Patient:
    """Load one patient, select features, forward-fill within the patient (time-ordered)."""
    df = pd.read_csv(path, sep="|")
    feats = df[FEATURES].astype("float32")
    feats = feats.ffill()  # past -> future only; no future leakage
    labels = df[LABEL].to_numpy(dtype=np.int64)
    return Patient(pid=path.stem, site=site, feats=feats.to_numpy(), labels=labels)


def split_patients(
    patients: list[Patient], val_frac: float, seed: int
) -> tuple[list[Patient], list[Patient]]:
    """Patient-level split, site-aware (A/B ratio preserved in both train and val)."""
    rng = np.random.default_rng(seed)
    train: list[Patient] = []
    val: list[Patient] = []
    for s in SETS:
        group = [p for p in patients if p.site == s]
        perm = rng.permutation(len(group))
        n_val = int(round(len(group) * val_frac))
        val_idx = set(perm[:n_val].tolist())
        for i, p in enumerate(group):
            (val if i in val_idx else train).append(p)
    return train, val


def compute_train_stats(train: list[Patient]) -> tuple[np.ndarray, np.ndarray]:
    """Per-feature nanmean/nanstd over TRAIN patient-hours (after forward-fill)."""
    stacked = np.concatenate([p.feats for p in train], axis=0)  # (sum_T, 10)
    mean = np.nanmean(stacked, axis=0)
    std = np.nanstd(stacked, axis=0)
    std = np.where(std < 1e-8, 1.0, std)  # guard constant columns
    return mean.astype("float32"), std.astype("float32")


def normalize_patient(p: Patient, mean: np.ndarray, std: np.ndarray) -> Patient:
    """mean-fill remaining NaN (train mean) then z-score (train mean/std)."""
    x = p.feats.copy()
    nan_mask = np.isnan(x)
    # fill remaining NaN with the train column mean
    x = np.where(nan_mask, mean[None, :], x)
    x = (x - mean[None, :]) / std[None, :]
    return Patient(pid=p.pid, site=p.site, feats=x.astype("float32"), labels=p.labels)
