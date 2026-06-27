"""H1-a — raw cache layer (결정 1·8).

Read each PhysioNet .psv ONCE and cache the 19 candidate feature columns with
NaN preserved (no fill — both the tree path [NaN as-is] and the GRU path [runtime
ffill] read from this one cache). Variable length (T×19) is preserved by storing
one .npz per patient.

This module builds the cache and verifies it against the H1-a PASS gate; it does
NOT split, impute, normalize, or window (those are H1-b).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from sepsis import config as C


@dataclass
class CacheStats:
    n_patients: int
    per_site: dict[str, int]
    feature_names: list[str]
    n_feature_cols: int
    total_rows: int
    lab_missing_pct: dict[str, float]
    label_value_violations: int  # patients with a label outside {0,1} (hard fail)
    label_block_violations: int  # block not contiguous/right-end (logged; excluded in H1-b)
    total_positive_patients: int


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
def _patient_files() -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for site in C.SITES:
        site_dir = C.DATA_DIR / site
        if not site_dir.is_dir():
            raise FileNotFoundError(f"missing data dir: {site_dir}")
        for p in sorted(site_dir.glob("*.psv")):
            files.append((site, p))
    return files


def _load_one(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Return (feats T×19 float32 with NaN preserved, labels T int8)."""
    # usecols speeds parsing; reindex to enforce CACHE_FEATURES order.
    df = pd.read_csv(path, sep="|", usecols=C.CACHE_FEATURES + [C.LABEL])
    feats = df[C.CACHE_FEATURES].to_numpy(dtype=np.float32)  # NaN preserved (no fill)
    labels = df[C.LABEL].to_numpy(dtype=np.int8)
    return feats, labels


def build_cache(cache_dir: Path | None = None, *, limit: int | None = None,
                progress_every: int = 5000) -> Path:
    """Build the per-patient raw cache. Overwrites any existing cache."""
    cache_dir = Path(cache_dir) if cache_dir else C.CACHE_DIR
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    files = _patient_files()
    if limit is not None:
        files = files[:limit]

    rows = []
    for i, (site, path) in enumerate(files, 1):
        feats, labels = _load_one(path)
        pid = path.stem
        out = cache_dir / site / f"{pid}.npz"
        out.parent.mkdir(parents=True, exist_ok=True)
        # self-contained per patient: feats, labels, pid, site
        np.savez(out, feats=feats, labels=labels,
                 pid=np.array(pid), site=np.array(site))
        rows.append({"pid": pid, "site": site, "n_timesteps": int(feats.shape[0]),
                     "n_pos": int((labels == 1).sum())})
        if i % progress_every == 0:
            print(f"[build] {i}/{len(files)} cached")

    manifest = pd.DataFrame(rows)
    manifest.to_parquet(cache_dir / "manifest.parquet")
    print(f"[build] done: {len(rows)} patients -> {cache_dir}")
    return cache_dir


# ---------------------------------------------------------------------------
# verify (H1-a PASS gate) — reads back from the CACHE, not build-time memory
# ---------------------------------------------------------------------------
def _label_block_ok(labels: np.ndarray) -> bool:
    """True if no positives, OR positives form a single contiguous block that
    ends at the last timestep (right-truncated). 결정 4 / EDA §6."""
    pos = np.flatnonzero(labels == 1)
    if pos.size == 0:
        return True
    contiguous = pos[-1] - pos[0] + 1 == pos.size
    ends_at_last = pos[-1] == labels.size - 1
    return bool(contiguous and ends_at_last)


def compute_stats(cache_dir: Path | None = None) -> CacheStats:
    cache_dir = Path(cache_dir) if cache_dir else C.CACHE_DIR
    manifest = pd.read_parquet(cache_dir / "manifest.parquet")

    lab_idx = {lab: C.CACHE_FEATURES.index(lab) for lab in C.LABS_9}
    nan_counts = {lab: 0 for lab in C.LABS_9}
    total_rows = 0
    feature_names: list[str] | None = None
    n_feature_cols = -1
    value_violations = 0
    block_violations = 0
    pos_patients = 0
    per_site = {s: 0 for s in C.SITES}

    for _, r in manifest.iterrows():
        site, pid = r["site"], r["pid"]
        z = np.load(cache_dir / site / f"{pid}.npz", allow_pickle=False)
        feats, labels = z["feats"], z["labels"]
        per_site[site] += 1
        n_feature_cols = feats.shape[1]
        total_rows += feats.shape[0]
        for lab, j in lab_idx.items():
            nan_counts[lab] += int(np.isnan(feats[:, j]).sum())
        if not np.isin(labels, (0, 1)).all():
            value_violations += 1
        if (labels == 1).any():
            pos_patients += 1
            if not _label_block_ok(labels):
                block_violations += 1

    # feature_names come from config (npz stores raw arrays); the gate checks the
    # cache was built with exactly this ordered set.
    feature_names = list(C.CACHE_FEATURES)
    lab_missing_pct = {lab: 100.0 * nan_counts[lab] / total_rows for lab in C.LABS_9}

    return CacheStats(
        n_patients=len(manifest),
        per_site=per_site,
        feature_names=feature_names,
        n_feature_cols=n_feature_cols,
        total_rows=total_rows,
        lab_missing_pct=lab_missing_pct,
        label_value_violations=value_violations,
        label_block_violations=block_violations,
        total_positive_patients=pos_patients,
    )


def verify_cache(cache_dir: Path | None = None, *, tol_pct: float = 0.5) -> tuple[bool, list[str], CacheStats]:
    """Run the 5 H1-a PASS asserts against the cache. Returns (ok, lines, stats).

    A failing check appends a FAIL line; building code should STOP on any FAIL.
    """
    cache_dir = Path(cache_dir) if cache_dir else C.CACHE_DIR
    stats = compute_stats(cache_dir)
    lines: list[str] = []
    ok = True

    def check(cond: bool, label: str, detail: str) -> None:
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        if not cond:
            ok = False
        lines.append(f"[{tag}] {label}: {detail}")

    # 1. patient count
    check(stats.n_patients == C.N_PATIENTS,
          "#1 patient count",
          f"{stats.n_patients} (expect {C.N_PATIENTS}); per-site {stats.per_site}")

    # 2. feature columns == 19, exact names; label/site/pid accompany
    names_ok = stats.feature_names == list(C.CACHE_FEATURES)
    check(stats.n_feature_cols == 19 and names_ok,
          "#2 feature cols == 19 (exact names) + label/site/pid",
          f"n_cols={stats.n_feature_cols}; names_match={names_ok}")

    # 3. excluded columns absent from cache
    excluded_present = [c for c in C.EXCLUDED_NONFEATURES if c in C.CACHE_FEATURES]
    check(len(excluded_present) == 0,
          "#3 excluded cols absent",
          f"excluded-in-cache={excluded_present or 'none'}")

    # 4. lab missing % within ±tol of EDA
    worst = 0.0
    worst_lab = ""
    for lab in C.LABS_9:
        diff = abs(stats.lab_missing_pct[lab] - C.EDA_LAB_MISSING_PCT[lab])
        if diff > worst:
            worst, worst_lab = diff, lab
    check(worst <= tol_pct,
          f"#4 lab missing% within ±{tol_pct}%p of EDA",
          f"max dev {worst:.3f}%p @ {worst_lab}")

    # 5. labels ∈ {0,1} (HARD) + positive block contiguous/right-end (LOGGED — 제외는 H1-b)
    check(stats.label_value_violations == 0,
          "#5 labels ∈ {0,1}",
          f"out-of-range-label patients={stats.label_value_violations}")
    lines.append(
        f"[LOG ] #5 positive block contiguous & right-truncated: "
        f"violations={stats.label_block_violations}/{stats.total_positive_patients} "
        f"positive patients (excluded later in H1-b)")

    return ok, lines, stats
