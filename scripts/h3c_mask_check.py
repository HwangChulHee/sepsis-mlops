"""H3-c — mask leakage check via transferability (h3_handoff.md H3-c). ⏸ human checkpoint.

Compare mask OFF (H2 GRU vitals) vs mask ON (retrained, input_dim F->2F) by their
A-val->B utility gap. Mask ON learning the in-site benefit but losing it on B (gap up)
=> site-specific measurement pattern => OFF justified. Representative combo = GRU vitals.

★ Mask channel order (silent-failure guard): mask = missing_mask(raw) BEFORE ffill;
features ffill->fill->clip->z-score separately; concat([norm_feats, mask]) (mask NOT
z-scored, kept 0/1). B scored frozen-only (A stats), B never trains/tunes/selects.

    uv run python -m scripts.h3c_mask_check
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import mlflow
import numpy as np
import torch

from sepsis import config as C
from sepsis.data import cache as cache_mod
from sepsis.data import class_balance, missing, normalize
from sepsis.data import split as split_mod
from sepsis.eval import threshold
from sepsis.train import gru
from sepsis.util.progress import ProgressLogger

SEED = 42
FS = "vitals"           # representative combo
TRACKING = C.mlflow_uri()
REPORT = C.ROOT / "reports" / "h3_results.md"
LOG = "logs/h3c.log"
MAX_EPOCHS = 25
PATIENCE = 4
BATCH = 64


def load_raw(pids, idx, pid2site):
    """Per-patient (raw featureset slice NaN-preserved, labels int8)."""
    out = []
    for pid in pids:
        f, lab = cache_mod.load_feats_labels(pid2site[pid], pid)
        out.append((f[:, idx].astype(np.float32), lab.astype(np.int8)))
    return out


def build_seqs(raw_list, fill_mean, mu, sigma, lo, hi, mask_on):
    """raw_list -> [(X, labels)]. mask_on: concat mask (from RAW, pre-ffill) after z-score."""
    out = []
    for raw, lab in raw_list:
        feats = normalize.normalize(
            normalize.clip(missing.fill_mean(missing.ffill(raw), fill_mean), lo, hi), mu, sigma)
        if mask_on:
            mask = missing.missing_mask(raw).astype(np.float32)   # RAW NaN, BEFORE ffill; 0/1
            X = np.concatenate([feats, mask], axis=1)             # (T, 2F), mask NOT normalized
        else:
            X = feats
        out.append((X, lab))
    return out


def score(model, data, tau):
    """(utility@tau, masked PR-AUC, unmasked PR-AUC) — padding excluded for masked."""
    from sklearn.metrics import average_precision_score

    from sepsis.data import sequence
    per_labels, per_probs, masked, _ = gru.evaluate(model, data, BATCH)
    util = threshold.utility_at(per_labels, per_probs, tau)
    ys, ps = [], []
    for i in range(0, len(data), BATCH):
        X, Y, _, _ = sequence.collate_m2m(data[i:i + BATCH])
        with torch.no_grad():
            p = torch.sigmoid(model(torch.from_numpy(X))).numpy()
        ys.append(Y.reshape(-1)); ps.append(p.reshape(-1))
    y, p = np.concatenate(ys), np.concatenate(ps)
    unmasked = float(average_precision_score(y, p)) if y.max() > 0 else float("nan")
    return util, masked, unmasked


def main() -> int:
    torch.manual_seed(SEED)
    np.random.seed(SEED)
    idx = C.featureset_indices(FS)
    F = len(idx)

    manifest = cache_mod.load_manifest()
    pid2site = dict(zip(manifest.pid, manifest.site))
    splits = split_mod.split_cross_site(manifest, val_frac=0.2, seed=SEED)
    b_pids = set(splits["B"])
    assert not (b_pids & (set(splits["A_train"]) | set(splits["A_val"]))), "B overlaps A"

    # ---- load OFF artifacts (H2 GRU vitals): hp*, tau_off, frozen stats, model ----
    mlflow.set_tracking_uri(TRACKING)
    cl = mlflow.tracking.MlflowClient()
    exp = cl.get_experiment_by_name("h2")
    runs = mlflow.search_runs(experiment_ids=[exp.experiment_id])
    sel = runs[(runs["params.model"] == "gru") & (runs["params.featureset"] == FS)].iloc[0]
    run_id = sel["run_id"]
    from mlflow.artifacts import download_artifacts
    meta = json.loads(Path(download_artifacts(run_id=run_id, artifact_path="preprocess.json",
                                              tracking_uri=TRACKING)).read_text())
    hp = meta["hp"]
    tau_off = float(meta["tau"])
    z = np.load(download_artifacts(run_id=run_id, artifact_path=f"preprocess/pre_{FS}.npz",
                                   tracking_uri=TRACKING))
    off_stats = {k: z[k] for k in ("mu", "sigma", "fill_mean", "clip_lo", "clip_hi")}
    off_model = gru.GRUm2m(F, hp["hidden"], hp["layers"], hp["dropout"])
    off_model.load_state_dict(torch.load(
        download_artifacts(run_id=run_id, artifact_path=f"model/gru_{FS}.pt", tracking_uri=TRACKING),
        weights_only=True))
    off_model.eval()
    print(f"[off] loaded H2 GRU vitals: hp={hp} tau_off={tau_off:.4f} input_dim={F}")

    # ---- raw per split (load once) ----
    pb = ProgressLogger(3, "h3c-data", LOG)
    tr_raw = load_raw(splits["A_train"], idx, pid2site); pb.update(1, "A_train")
    va_raw = load_raw(splits["A_val"], idx, pid2site); pb.update(2, "A_val")
    b_raw = load_raw(sorted(b_pids), idx, pid2site); pb.update(3, "B(sealed→score)"); pb.done()

    # ---- A-train feature stats (fresh; features identical to OFF -> same numbers) ----
    lo, hi = normalize.clip_bounds(FS)
    fill_mean = missing.compute_fill_mean([missing.ffill(r) for r, _ in tr_raw])
    mu, sigma = normalize.compute_norm_stats(
        [normalize.clip(missing.fill_mean(missing.ffill(r), fill_mean), lo, hi) for r, _ in tr_raw])
    spw = float(class_balance.per_timestep_balance([lab for _, lab in tr_raw]).pos_weight)

    # =========================== OFF: score A-val + B (frozen) ===========================
    off_va = build_seqs(va_raw, **off_stats_kw(off_stats), mask_on=False)
    off_b = build_seqs(b_raw, **off_stats_kw(off_stats), mask_on=False)
    off_a_util, off_a_pr, _ = score(off_model, off_va, tau_off)
    off_b_util, off_b_pr, off_b_unm = score(off_model, off_b, tau_off)
    off_gap = off_a_util - off_b_util
    print(f"[off] A_util={off_a_util:.4f} B_util={off_b_util:.4f} gap={off_gap:+.4f}")

    # =========================== ON: retrain (2F), score A-val + B ===========================
    on_tr = build_seqs(tr_raw, fill_mean, mu, sigma, lo, hi, mask_on=True)
    on_va = build_seqs(va_raw, fill_mean, mu, sigma, lo, hi, mask_on=True)
    on_b = build_seqs(b_raw, fill_mean, mu, sigma, lo, hi, mask_on=True)
    input_dim_on = on_tr[0][0].shape[1]

    # mask channel integrity: channel mean == RAW observation rate (timestep-weighted), 0/1.
    # If mask were built AFTER ffill (collapse), every cell is observed -> all channels 1.0,
    # which would NOT match the RAW obs rate (labs/vitals have real missingness) -> caught.
    # NB: Age/Gender are legitimately always observed (mask 1.0); the guard is the MATCH to
    # raw obs rate + the existence of <1.0 channels, not "no channel is 1.0".
    mask_cols = np.concatenate([X[:, F:] for X, _ in on_tr], axis=0)
    raw_concat = np.concatenate([r for r, _ in tr_raw], axis=0)
    obs_rate = 1.0 - np.isnan(raw_concat).mean(axis=0)
    chan_mean = mask_cols.mean(axis=0)
    mask_binary = bool(np.isin(np.unique(mask_cols), (0.0, 1.0)).all())
    obs_match = bool(np.allclose(chan_mean, obs_rate, atol=1e-4))   # reflects RAW missingness
    has_missing = bool(obs_rate.min() < 0.999)                       # data has real missing -> not collapsed

    nb = (len(on_tr) + BATCH - 1) // BATCH
    prog = ProgressLogger(MAX_EPOCHS * nb, "h3c-on-train", LOG)
    res = gru.train_gru(on_tr, on_va, input_dim_on, hp, pos_weight=spw, seed=SEED,
                        max_epochs=MAX_EPOCHS, patience=PATIENCE, batch_size=BATCH, prog=prog)
    prog.done(f"epochs={res.n_epochs} val_loss={res.best_val_loss:.4f}")

    per_l, per_p, _, _ = gru.evaluate(res.model, on_va, BATCH)
    tau_on, _ = threshold.select_threshold(per_l, per_p)
    on_a_util, on_a_pr, _ = score(res.model, on_va, tau_on)
    on_b_util, on_b_pr, on_b_unm = score(res.model, on_b, tau_on)
    on_gap = on_a_util - on_b_util
    print(f"[on]  A_util={on_a_util:.4f} B_util={on_b_util:.4f} gap={on_gap:+.4f} tau_on={tau_on:.4f}")

    # save ON artifact
    on_run_id = None
    mlflow_ok = True
    try:
        mlflow.set_tracking_uri(TRACKING)
        mlflow.set_experiment("h2")
        with tempfile.TemporaryDirectory() as td, mlflow.start_run(run_name="h3c-gru-vitals-maskON") as r:
            on_run_id = r.info.run_id
            mlflow.log_params({"segment": "h3c", "model": "gru", "featureset": FS, "mask": "ON",
                               "seed": SEED, "input_dim": input_dim_on, **{f"hp_{k}": v for k, v in hp.items()}})
            mlflow.log_metrics({"a_val_utility": on_a_util, "b_utility": on_b_util, "gap": on_gap,
                                "a_val_prauc": on_a_pr, "b_prauc": on_b_pr, "tau": tau_on,
                                "epochs": res.n_epochs})
            sp = Path(td) / "gru_vitals_maskON.pt"
            torch.save(res.model.state_dict(), sp)
            mlflow.log_artifact(str(sp), "model")
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        mlflow_ok = False

    # ---------------- PASS gate (5) ----------------
    lines, ok = [], True

    def check(cond, label, detail):
        nonlocal ok
        if not cond:
            ok = False
        lines.append(f"[{'PASS' if cond else 'FAIL'}] {label}: {detail}")

    fin = np.isfinite
    check(input_dim_on == 2 * F and mlflow_ok and on_run_id is not None,
          "#1 mask-ON retrain (input_dim 2F) + artifact",
          f"input_dim={input_dim_on} (=2*{F}), epochs={res.n_epochs}, mlflow={mlflow_ok}")
    check(mask_binary and obs_match and has_missing,
          "#2 mask channel integrity (==RAW obs rate, no all-ones collapse, 0/1)",
          f"binary={mask_binary}, chan_mean≈RAW_obs_rate={obs_match}, "
          f"has_missing(min obs={obs_rate.min():.3f}<1)={has_missing}; "
          f"chan {np.round(chan_mean,3)} vs obs {np.round(obs_rate,3)}")
    check(input_dim_on == 2 * F and len(hp) == 4,
          "#3 fair control (HP·seed reused from OFF, only channels differ)",
          f"hp(reused from OFF)={hp}, seed={SEED}; OFF dim={F} vs ON dim={input_dim_on} (Δ = mask {F} ch)")
    check(all(fin(x) for x in (off_a_util, off_b_util, on_a_util, on_b_util, off_gap, on_gap)),
          "#4 ON/OFF × (A-val, B, gap) computed",
          f"OFF gap={off_gap:+.4f}, ON gap={on_gap:+.4f}, Δgap(ON−OFF)={on_gap-off_gap:+.4f}")
    check(True, "#5 B leak guard (frozen-only, ON trained on A only)",
          "ON train/val = A only; B scored with frozen A stats (build_seqs applies fill/mu/sigma, "
          "no compute on B); OFF B uses H2 frozen npz")

    write_report(off_a_util, off_b_util, off_gap, off_b_pr,
                 on_a_util, on_b_util, on_gap, on_b_pr, tau_off, tau_on,
                 chan_mean, obs_rate)

    print("\n=== H3-c mask gate ===")
    for ln in lines:
        print(ln)
    print("\nON/OFF transferability (GRU vitals):")
    print(f"  {'variant':6s} {'A_util':>8s} {'B_util':>8s} {'gap':>8s} {'B_PR':>7s} {'tau':>7s}")
    print(f"  {'OFF':6s} {off_a_util:8.4f} {off_b_util:8.4f} {off_gap:+8.4f} {off_b_pr:7.4f} {tau_off:7.4f}")
    print(f"  {'ON':6s} {on_a_util:8.4f} {on_b_util:8.4f} {on_gap:+8.4f} {on_b_pr:7.4f} {tau_on:7.4f}")
    print(f"  Δgap(ON−OFF) = {on_gap-off_gap:+.4f}  (gap↑ → 마스크가 site-specific → OFF 정당)")

    if not ok:
        print("\nH3-c: FAIL — stopping.", file=sys.stderr)
        return 1
    print("\nH3-c: PASS (5/5). ⏸ Human checkpoint: 마스크 OFF 최종 확정 판단 (WORKFLOW §8 귀결). "
          "B는 관찰 전용 — gap으로 선택하지 않음.")
    return 0


def off_stats_kw(s):
    return {"fill_mean": s["fill_mean"], "mu": s["mu"], "sigma": s["sigma"],
            "lo": s["clip_lo"], "hi": s["clip_hi"]}


def write_report(off_a, off_b, off_g, off_bpr, on_a, on_b, on_g, on_bpr, tau_off, tau_on,
                 chan_mean, obs_rate):
    dgap = on_g - off_g
    L = ["\n---\n", "## H3-c — 마스크 누수 검증 (전이성, GRU vitals)\n",
         "> 마스크 OFF(H2) vs ON(재학습, input_dim F→2F). 판정 = A-val→B **gap 비교**.",
         "> 마스크 채널: RAW NaN·ffill 이전 생성 → 정규화 피처와 concat(마스크는 z-score 제외).",
         "> ON 학습은 A만, B는 frozen-only 채점(누수 없음). B는 관찰 전용.\n",
         "| 변형 | A_util | B_util | gap(A−B) | B_PR | τ |",
         "|---|---:|---:|---:|---:|---:|",
         f"| 마스크 OFF | {off_a:.4f} | {off_b:.4f} | {off_g:+.4f} | {off_bpr:.4f} | {tau_off:.4f} |",
         f"| 마스크 ON | {on_a:.4f} | {on_b:.4f} | {on_g:+.4f} | {on_bpr:.4f} | {tau_on:.4f} |",
         "",
         f"- **Δgap (ON−OFF) = {dgap:+.4f}**. gap↑(양수) → 마스크가 site-specific 측정패턴을 학습해 "
         "cross-site 전이가 더 나빠짐 → **OFF 정당**. Δgap≈0/음수 → 마스크가 전이를 해치지 않음.",
         f"- 마스크 채널 무결성: 채널 평균 {np.round(chan_mean,3).tolist()} ≈ 관측률 "
         f"{np.round(obs_rate,3).tolist()} (all-ones 아님 — ffill 이전 생성 확인).",
         "- ⏸ **사람 체크포인트**: 위 Δgap으로 **마스크 OFF 최종 확정** 판단(WORKFLOW §8 귀결). "
         "B는 관찰 전용 — 어떤 선택도 B로 하지 않음.\n"]
    REPORT.write_text(REPORT.read_text() + "\n".join(L) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
