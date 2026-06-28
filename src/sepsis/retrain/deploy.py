"""H4r-c — safe versioned swap + rollback (handoff H4r-c, DDD 결정 5).

Materializes a retrained model into a VERSIONED bundle dir (model+stats+τ+reference, same
version), then swaps the active alias `gru_<fs>` to it — but ONLY after the validation gate
passes AND a human approves (swap raises otherwise; no module auto-calls swap). Rollback =
point the alias back to the previous version → model, preprocessing, τ, AND drift reference
all revert together (reference is in the bundle). Drift monitoring reads the active alias's
reference.npz, so rollback restores the matching baseline (no false drift).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import torch

from sepsis import config as C
from sepsis.data import cache as cache_mod
from sepsis.drift import reference as R
from scripts import h4s_export_bundle as export_mod

ARTIFACTS = C.ROOT / "deploy" / "artifacts"


def materialize(retrain_result, version: str, *, root: Path = ARTIFACTS) -> Path:
    """Write a retrained bundle to gru_<fs>@<version> (model+stats+τ+reference). No alias swap."""
    rr = retrain_result
    out = root / f"gru_{rr.featureset}@{version}"
    out.mkdir(parents=True, exist_ok=True)
    torch.save(rr.model.state_dict(), out / "model.pt")
    np.savez(out / "pre.npz", **rr.stats)
    (out / "meta.json").write_text(json.dumps(
        {"featureset": rr.featureset, "hp": rr.hp, "input_dim": rr.input_dim,
         "tau": rr.tau, "version": version, "trained_on": "A-train+B-retrain"}, indent=2))
    # reference IN the bundle = NEW training distribution (A-train + B-retrain)
    manifest = cache_mod.load_manifest()
    pid2site = dict(zip(manifest.pid, manifest.site))
    ref = R.build_reference_from_pids(rr.featureset, rr.train_pids, pid2site)
    R.save_reference(ref, out / "reference.npz")
    return out


def active_version(featureset: str, *, root: Path = ARTIFACTS) -> str | None:
    link = Path(root) / f"gru_{featureset}"
    return os.readlink(link) if link.is_symlink() else None


def set_active(featureset: str, version_dir: Path, *, root: Path = ARTIFACTS) -> None:
    export_mod.set_alias(Path(root), f"gru_{featureset}", Path(version_dir).name)


def swap(featureset: str, version_dir: Path, *, validation, approved: bool,
         root: Path = ARTIFACTS) -> str | None:
    """Activate version_dir ONLY if validation passed AND a human approved. Returns the
    PREVIOUS active version name (for rollback). Raises otherwise."""
    if approved is not True:
        raise PermissionError("human approval required: swap blocked (approved is not True)")
    if not getattr(validation, "no_regression", False):
        raise ValueError("validation gate failed (A-val regression) — swap blocked")
    prev = active_version(featureset, root=root)
    set_active(featureset, version_dir, root=root)
    return prev


def rollback(featureset: str, previous_version_name: str, *, root: Path = ARTIFACTS) -> None:
    """Point the alias back to a previous version dir name (model+stats+τ+reference revert)."""
    export_mod.set_alias(Path(root), f"gru_{featureset}", previous_version_name)


# --- drift monitor reads the reference of the ACTIVE bundle (via alias) ---
def active_reference_path(featureset: str, *, root: Path = ARTIFACTS) -> Path:
    return Path(root) / f"gru_{featureset}" / "reference.npz"


def active_reference(featureset: str, *, root: Path = ARTIFACTS) -> R.Reference:
    """The drift baseline the monitor should use — always matches the deployed model."""
    return R.load_reference(active_reference_path(featureset, root=root))
