"""H4r-c — safe versioned swap + rollback (handoff H4r-c, DDD 결정 5).

Materializes a retrained model into a VERSIONED bundle dir (model+stats+τ+reference, same
version), then swaps the active alias `gru_<fs>` to it — but ONLY after the validation gate
passes AND a human approves (swap raises otherwise; no module auto-calls swap). Rollback =
point the alias back to the previous version → model, preprocessing, τ, AND drift reference
all revert together (reference is in the bundle). Drift monitoring reads the active alias's
reference.npz, so rollback restores the matching baseline (no false drift).
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from pathlib import Path

import numpy as np
import torch

from sepsis import config as C
from sepsis.data import cache as cache_mod
from sepsis.drift import reference as R
from sepsis.serve import bundle as bundle_mod

ARTIFACTS = C.ROOT / "deploy" / "artifacts"


def _atomic_write_json(path: Path, obj) -> None:
    """Write JSON atomically: body to a temp file, then os.replace onto the target. A reader
    sees either the OLD file or the COMPLETE new file — never a torn/partial write (결정 7)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    os.replace(tmp, path)   # atomic rename — torn read 방지


def materialize(retrain_result, version: str, *, validation, root: Path = ARTIFACTS) -> Path:
    """Write a retrained bundle to gru_<fs>@<version> (model+stats+τ+reference) PLUS validation.json
    + retrain.json, finalized by a `.ready` marker (결정 1·2·7). No alias swap.

    Called REGARDLESS of the validation gate (MJ-b): a REGRESSED version is still materialized so
    the console can show it as a challenger. The gate is enforced only in swap()."""
    rr = retrain_result
    out = root / f"gru_{rr.featureset}@{version}"
    out.mkdir(parents=True, exist_ok=True)
    torch.save(rr.model.state_dict(), out / "model.pt")
    np.savez(out / "pre.npz", **rr.stats)
    (out / "meta.json").write_text(json.dumps(
        {"featureset": rr.featureset, "hp": rr.hp, "input_dim": rr.input_dim,
         "tau": rr.tau, "version": version, "trained_on": "A-train+B-retrain",
         "run_id": rr.run_id}, indent=2))   # ADDED — MLflow 연결 키의 단일 권위 출처(결정 4)
    # reference IN the bundle = NEW training distribution (A-train + B-retrain)
    manifest = cache_mod.load_manifest()
    pid2site = dict(zip(manifest.pid, manifest.site))
    ref = R.build_reference_from_pids(rr.featureset, rr.train_pids, pid2site)
    R.save_reference(ref, out / "reference.npz")

    # --- validation.json·retrain.json 원자 co-visible 영속 + .ready AND 완성 표식 (결정 1·2·7) ---
    val = {**dataclasses.asdict(validation),                          # eps 포함(구현 3-pre, MJ-c)
           "validated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}  # UTC, Z 접미사(mn5)
    rj = {"epochs": rr.epochs, "val_loss": rr.val_loss, "b_split_seed": rr.seed,
          "n_train_pids": len(rr.train_pids), "n_b_retrain": len(rr.b_retrain),
          "n_b_holdout": len(rr.b_holdout), "run_id": rr.run_id, "git_commit": rr.git_commit}
    _atomic_write_json(out / "validation.json", val)
    _atomic_write_json(out / "retrain.json", rj)
    _atomic_write_json(out / ".ready", {"complete": True})   # 마지막 — 두 JSON 완전할 때만 노출
    return out


def active_version(featureset: str, *, root: Path = ARTIFACTS) -> str | None:
    link = Path(root) / f"gru_{featureset}"
    return os.readlink(link) if link.is_symlink() else None


def set_active(featureset: str, version_dir: Path, *, root: Path = ARTIFACTS) -> None:
    bundle_mod.set_alias(Path(root), f"gru_{featureset}", Path(version_dir).name)


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


def rollback(featureset: str, previous_version_name: str, *, approved: bool,
             root: Path = ARTIFACTS) -> str | None:
    """Point the alias back to a previous version dir name (model+stats+τ+reference revert).

    Symmetric with swap() (H4r 방어 심화, BR2-1): requires human approval and returns the
    PREVIOUS active version name. The console API (service.rollback) still enforces the
    archived-target gate before calling this — this `approved` guard is the defense-in-depth
    backstop against callers that bypass the console API and import deploy.rollback directly."""
    if approved is not True:
        raise PermissionError("human approval required: rollback blocked (approved is not True)")
    prev = active_version(featureset, root=root)
    bundle_mod.set_alias(Path(root), f"gru_{featureset}", previous_version_name)
    return prev


# --- drift monitor reads the reference of the ACTIVE bundle (via alias) ---
def active_reference_path(featureset: str, *, root: Path = ARTIFACTS) -> Path:
    return Path(root) / f"gru_{featureset}" / "reference.npz"


def active_reference(featureset: str, *, root: Path = ARTIFACTS) -> R.Reference:
    """The drift baseline the monitor should use — always matches the deployed model."""
    return R.load_reference(active_reference_path(featureset, root=root))
