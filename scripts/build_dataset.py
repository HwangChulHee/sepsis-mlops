"""H1-b runner — transform pipeline + 10-assert PASS gate (결정 2·3·4·5·6·7·8 + 부속).

Wires split -> missing -> normalize -> sequence / features / class_balance per the
runtime order (결정 8), then runs the 10 asserts on the CROSS_SITE split (the
leakage frontier). Also confirms the unified split is well-formed.

    uv run python -m scripts.build_dataset                       # full gate (cross_site)
    uv run python -m scripts.build_dataset --featureset vitals   # 9-feature set
    uv run python -m scripts.build_dataset --limit-tree 4000     # cap tree pass (wiring)

STOP on any FAIL: exits non-zero, do not proceed to re-smoke.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np

from sepsis import config as C
from sepsis.data import cache as cache_mod
from sepsis.data import class_balance, features, missing, normalize, sequence, split


# ---------------------------------------------------------------------------
# load + GRU transform helpers
# ---------------------------------------------------------------------------
def load_all(featureset: str):
    """pid -> (raw feats [T×F, NaN preserved], labels). Sliced to the model featureset."""
    manifest = cache_mod.load_manifest()
    idx = C.featureset_indices(featureset)
    site_of = dict(zip(manifest.pid, manifest.site))
    raw, labels = {}, {}
    for pid, site in site_of.items():
        f, lab = cache_mod.load_feats_labels(site, pid)
        raw[pid] = f[:, idx].astype(np.float32)
        labels[pid] = lab
    return raw, labels, site_of


def gru_clip(raw_arr, fill_mean, lo, hi):
    """raw -> ffill -> train-mean fill -> clip (pre-normalization)."""
    return normalize.clip(missing.fill_mean(missing.ffill(raw_arr), fill_mean), lo, hi)


def split_colmean(pids, raw, fill_mean, lo, hi):
    """Streaming per-feature mean of post-clip data over a split (for assert #3)."""
    total = None
    n = 0
    for pid in pids:
        c = gru_clip(raw[pid], fill_mean, lo, hi)
        s = c.sum(axis=0, dtype=np.float64)
        total = s if total is None else total + s
        n += c.shape[0]
    return total / n


# ---------------------------------------------------------------------------
# gate
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--featureset", default="vitals_labs", choices=list(C.FEATURESETS))
    ap.add_argument("--mode", default="cross_site", choices=["cross_site", "unified"])
    ap.add_argument("--val-frac", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--limit-tree", type=int, default=None, help="cap patients in tree pass (#8 wiring)")
    cfg = ap.parse_args()

    lines: list[str] = []
    ok = True

    def check(cond, label, detail):
        nonlocal ok
        if not cond:
            ok = False
        lines.append(f"[{'PASS' if cond else 'FAIL'}] {label}: {detail}")

    print(f"[load] featureset={cfg.featureset} ...")
    raw, labels, site_of = load_all(cfg.featureset)
    manifest = cache_mod.load_manifest()

    # --- splits ---
    splits = split.split_cross_site(manifest, val_frac=cfg.val_frac, seed=cfg.seed)
    uni = split.split_unified(manifest, seed=cfg.seed)  # confirm well-formed
    train_name = "A_train"
    tr, va, te = splits["A_train"], splits["A_val"], splits["B"]
    print(f"[split] cross_site A_train={len(tr)} A_val={len(va)} B={len(te)} "
          f"(val_frac={cfg.val_frac}, seed={cfg.seed})")

    lo, hi = normalize.clip_bounds(cfg.featureset)

    # --- train-only stats (A-train) ---
    fill_mean = missing.compute_fill_mean([missing.ffill(raw[p]) for p in tr])
    mu, sigma = normalize.compute_norm_stats([gru_clip(raw[p], fill_mean, lo, hi) for p in tr])

    # === assert #1: patient leakage (3 splits disjoint) ===
    s_tr, s_va, s_te = set(tr), set(va), set(te)
    inter = (s_tr & s_va) | (s_tr & s_te) | (s_va & s_te)
    check(len(inter) == 0, "#1 patient leakage (3 splits disjoint)", f"intersection={len(inter)}")

    # === assert #2: target seal (B not in A-train/A-val) ===
    leaked_b = (s_te & s_tr) | (s_te & s_va)
    check(len(leaked_b) == 0, "#2 target seal (setB ∉ A-train/A-val)", f"B-in-train/val={len(leaked_b)}")

    # === assert #3: train-only normalization ===
    mean_tr = split_colmean(tr, raw, fill_mean, lo, hi)
    mean_va = split_colmean(va, raw, fill_mean, lo, hi)
    mean_te = split_colmean(te, raw, fill_mean, lo, hi)
    # μ stored float32 (≈1e-5 abs error at feature scale); split gaps are ≫1e-4.
    eq_tr = np.allclose(mu.astype(np.float64), mean_tr, atol=1e-4, rtol=0)
    ne_va = not np.allclose(mu.astype(np.float64), mean_va, atol=1e-4, rtol=0)
    ne_te = not np.allclose(mu.astype(np.float64), mean_te, atol=1e-4, rtol=0)
    check(eq_tr and ne_va and ne_te, "#3 train-only normalization",
          f"μ==mean(train)={eq_tr}, μ!=mean(val)={ne_va}, μ!=mean(test)={ne_te}")

    # === assert #4: mask built from RAW NaN (before ffill) ===
    mask_ok = True
    for pid in raw:
        m = missing.missing_mask(raw[pid])         # 1=obs, 0=missing
        if not np.array_equal(m == 0, np.isnan(raw[pid])):
            mask_ok = False
            break
    check(mask_ok, "#4 mask order (mask 0 == raw NaN, pre-ffill)", f"all_patients_match={mask_ok}")

    # === assert #6: imputation in raw space (no NaN; filled == ffill/mean, not 0) ===
    impute_ok = True
    used_zero_fill = False
    for pid in raw:
        ff = missing.ffill(raw[pid])
        filled = missing.fill_mean(ff, fill_mean)
        expected = np.where(np.isnan(ff), fill_mean[None, :], ff)
        if np.isnan(filled).any() or not np.array_equal(filled, expected):
            impute_ok = False
            break
        # positions filled because raw was NaN got fill_mean (train), never literal 0
        filled_positions = np.isnan(ff)
        if filled_positions.any() and (fill_mean == 0).any():
            used_zero_fill = True
    check(impute_ok and not used_zero_fill, "#6 imputation raw-space (no NaN, ffill/mean not 0)",
          f"reconstruct_match={impute_ok}, zero_fill={used_zero_fill}")

    # === assert #5: validity mask consistency (sample batch) ===
    sample_pids = tr[:128]
    batch = [(normalize.normalize(gru_clip(raw[p], fill_mean, lo, hi), mu, sigma), labels[p].astype(np.float32))
             for p in sample_pids]
    X, Y, V, lengths = sequence.collate_m2m(batch)
    valid_ok = np.array_equal(V.sum(axis=1), lengths) and X.shape[0] == len(sample_pids)
    check(valid_ok, "#5 validity mask == real timesteps", f"sum==lengths={np.array_equal(V.sum(axis=1), lengths)}")

    # === assert #7: unidirectional GRU ===
    sequence.assert_causal()
    check(C.GRU_BIDIRECTIONAL is False, "#7 GRU bidirectional=False", f"{C.GRU_BIDIRECTIONAL}")

    # === assert #8: tree rows == total timesteps (no initial-timestep drop) ===
    tree_pids = list(raw)
    if cfg.limit_tree:
        tree_pids = tree_pids[:cfg.limit_tree]
    tree_rows = 0
    per_patient_ok = True
    expected_dim = len(C.featureset_columns(cfg.featureset)) * len(C.TREE_STATS)
    for pid in tree_pids:
        s = features.lookback_summary(raw[pid])
        if s.shape[0] != raw[pid].shape[0] or s.shape[1] != expected_dim:
            per_patient_ok = False
            break
        tree_rows += s.shape[0]
    expected_rows = sum(raw[p].shape[0] for p in tree_pids)
    check(per_patient_ok and tree_rows == expected_rows,
          "#8 tree rows == total timesteps (per-timestep, no drop)",
          f"rows={tree_rows} expected={expected_rows} dim={expected_dim} "
          f"{'(LIMITED)' if cfg.limit_tree else ''}")

    # === assert #9: pos_weight input (A-train per-timestep, plausible 1-4%, train-only) ===
    bal = class_balance.per_timestep_balance([labels[p] for p in tr])
    pct = bal.pos_ratio * 100
    pw_ok = np.isfinite(bal.pos_weight) and bal.pos_weight > 0
    check(1.0 <= pct <= 4.0 and pw_ok, "#9 pos_weight input (A-train, 1-4%, finite>0)",
          f"pos_ratio={pct:.3f}% pos_weight={bal.pos_weight:.2f} pos={bal.n_pos} total={bal.n_total}")

    # === assert #10: featureset derivation (vitals ⊂ vitals_labs, EtCO2 excluded) ===
    v9, v18 = set(C.FEATURESET_VITALS), set(C.FEATURESET_VITALS_LABS)
    subset = v9.issubset(v18)
    etco2_excluded = C.ETCO2 not in v9 and C.ETCO2 not in v18
    check(subset and etco2_excluded and len(v9) == 9 and len(v18) == 18,
          "#10 featureset derivation (9⊂18, EtCO2 excluded)",
          f"subset={subset} etco2_excluded={etco2_excluded} sizes={len(v9)}/{len(v18)}")

    # --- report ---
    print("\n=== H1-b PASS gate (cross_site) ===")
    for ln in lines:
        print(ln)
    print("\n=== split / pipeline stats ===")
    print(f"cross_site: A_train={len(tr)} A_val={len(va)} B(sealed)={len(te)} patients")
    print(f"unified:    train={len(uni['train'])} val={len(uni['val'])} test={len(uni['test'])} patients")
    print(f"timesteps:  A_train={sum(raw[p].shape[0] for p in tr):,} "
          f"A_val={sum(raw[p].shape[0] for p in va):,} B={sum(raw[p].shape[0] for p in te):,}")
    print(f"A_train per-timestep positive ratio = {bal.pos_ratio*100:.3f}%  "
          f"(pos={bal.n_pos:,}/{bal.n_total:,}) -> pos_weight = {bal.pos_weight:.2f}")
    print(f"featureset={cfg.featureset} ({len(C.featureset_columns(cfg.featureset))} feats); "
          f"tree summary dim={expected_dim}")

    if not ok:
        print("\nH1-b: FAIL — stopping. Do not proceed to re-smoke.", file=sys.stderr)
        return 1
    print("\nH1-b: PASS (10/10). Transform pipeline validated (cross_site).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
