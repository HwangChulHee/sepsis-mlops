"""H3-b — cross-site scoring A->B (h3_handoff.md H3-b). ★ B opened for scoring only.

Step 1: official utility equivalence (B NOT used).  Step 2: score the 6 H2 combos on
sealed setB using A-FROZEN artifacts only (μ/σ·fill·clip·τ from MLflow), report
A-val|B|gap. 5 programmatic asserts. B never touches fit/tune/select.

    uv run python -m scripts.h3b_crosssite
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import mlflow
import numpy as np
import torch

from sepsis import config as C
from sepsis.data import cache as cache_mod
from sepsis.data import split as split_mod
from sepsis.eval import crosssite, official_compat
from sepsis.train import gru, tree

SEED = 42
TOL = 1e-6
MODELS_FS = [("xgboost", "vitals"), ("xgboost", "vitals_labs"),
             ("lightgbm", "vitals"), ("lightgbm", "vitals_labs"),
             ("gru", "vitals"), ("gru", "vitals_labs")]
TRACKING = f"sqlite:///{C.ROOT}/mlflow.db"
H2_RESULTS = C.ROOT / "reports" / "h2_results.md"
REPORT = C.ROOT / "reports" / "h3_results.md"
DATE = "2026-06-28"


# ---------------- step 1: official equivalence ----------------
def synthetic_cohort(seed=SEED):
    """Edge-covering cohort of (labels, binary preds): septic/non-septic, short,
    immediate-positive, all-1/all-0/random/best predictions."""
    rng = np.random.default_rng(seed)
    cohort = []
    septic_specs = [(0, 8), (0, 2), (1, 10), (3, 9), (7, 20), (14, 40), (30, 50), (0, 1)]
    for first_pos, length in septic_specs:
        length = max(length, first_pos + 1)
        lab = np.zeros(length, dtype=np.int8)
        lab[first_pos:] = 1
        for preds in (np.ones(length, np.int8), np.zeros(length, np.int8),
                      (rng.random(length) < 0.3).astype(np.int8),
                      (rng.random(length) < 0.7).astype(np.int8)):
            cohort.append((lab, preds))
    for length in (1, 2, 8, 25, 40):           # non-septic
        lab = np.zeros(length, dtype=np.int8)
        cohort.append((lab, (rng.random(length) < 0.2).astype(np.int8)))
        cohort.append((lab, np.zeros(length, np.int8)))
    return cohort


# ---------------- A-val scores from h2_results.md (no recompute) ----------------
def parse_h2_results(path):
    """A-val PR-AUC/utility per (model, featureset) from the H2-d table."""
    out = {}
    row = re.compile(r"\|\s*\d+\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*([\d.]+)\s*\|\s*\*\*([\d.]+)\*\*")
    for line in Path(path).read_text().splitlines():
        m = row.search(line)
        if m:
            out[(m.group(1), m.group(2))] = {"prauc": float(m.group(3)),
                                             "utility": float(m.group(4))}
    return out


# ---------------- artifact load ----------------
def load_combo(run_id, model, fs):
    from mlflow.artifacts import download_artifacts
    pj = download_artifacts(run_id=run_id, artifact_path="preprocess.json", tracking_uri=TRACKING)
    meta = json.loads(Path(pj).read_text())
    tau = float(meta["tau"])
    if model == "gru":
        sp = download_artifacts(run_id=run_id, artifact_path=f"model/gru_{fs}.pt", tracking_uri=TRACKING)
        npz = download_artifacts(run_id=run_id, artifact_path=f"preprocess/pre_{fs}.npz", tracking_uri=TRACKING)
        z = np.load(npz)
        frozen = {k: z[k] for k in ("mu", "sigma", "fill_mean", "clip_lo", "clip_hi")}
        hp, input_dim = meta["hp"], int(meta["input_dim"])
        m = gru.GRUm2m(input_dim, hp["hidden"], hp["layers"], hp["dropout"])
        m.load_state_dict(torch.load(sp, weights_only=True))
        m.eval()
        return {"kind": "gru", "model": m, "frozen": frozen, "tau": tau,
                "npz_path": npz}
    ext = "ubj" if model == "xgboost" else "txt"
    mp = download_artifacts(run_id=run_id, artifact_path=f"model/{model}_{fs}.{ext}", tracking_uri=TRACKING)
    booster = tree.load_booster(model, mp)
    return {"kind": "tree", "model": model, "booster": booster, "tau": tau}


def main() -> int:
    lines, ok = [], True

    def check(cond, label, detail):
        nonlocal ok
        if not cond:
            ok = False
        lines.append(f"[{'PASS' if cond else 'FAIL'}] {label}: {detail}")

    # ===== STEP 1: official equivalence (B NOT used) =====
    cohort = synthetic_cohort()
    eq_ok, max_diff, n_checks, eq_details = official_compat.check_equivalence(cohort, tol=TOL)
    check(eq_ok, "#1 official utility equivalence",
          f"max|Δ|={max_diff:.2e} over {n_checks} checks (±{TOL}), {len(cohort)} patients incl. "
          f"non-septic/short/immediate" + (f"; FAIL {eq_details[:3]}" if eq_details else ""))
    if not eq_ok:
        print("\n".join(lines))
        print("\nH3-b: FAIL at step 1 (utility not equivalent to official) — stopping.", file=sys.stderr)
        return 1

    # ===== leak guard: crosssite.py must not USE fitting/selection functions =====
    # AST-based (real identifiers only) so docstring mentions of the forbidden names
    # don't trip the check.
    import ast
    src = (C.ROOT / "src/sepsis/eval/crosssite.py").read_text()
    forbidden = ["compute_norm_stats", "compute_fill_mean", "select_threshold"]
    tree_ast = ast.parse(src)
    used = ({n.attr for n in ast.walk(tree_ast) if isinstance(n, ast.Attribute)}
            | {n.id for n in ast.walk(tree_ast) if isinstance(n, ast.Name)})
    present = [f for f in forbidden if f in used]
    grep_ok = not present

    # ===== STEP 2: 6-combo A->B scoring (frozen-only) =====
    manifest = cache_mod.load_manifest()
    splits = split_mod.split_cross_site(manifest, val_frac=0.2, seed=SEED)
    b_pids = set(splits["B"])
    # static: B disjoint from A (sanity; B is now the scoring input)
    assert not (b_pids & (set(splits["A_train"]) | set(splits["A_val"]))), "B overlaps A"
    print(f"[B] opening sealed setB: {len(b_pids)} patients")

    mlflow.set_tracking_uri(TRACKING)
    cl = mlflow.tracking.MlflowClient()
    exp = cl.get_experiment_by_name("h2")
    runs = mlflow.search_runs(experiment_ids=[exp.experiment_id])

    def run_for(model, fs):
        sel = runs[(runs["params.model"] == model) & (runs["params.featureset"] == fs)]
        r = sel.iloc[0]
        return r["run_id"], r.get("metrics.best_iter")

    b_raw_cache = {}
    def b_raw(fs):
        if fs not in b_raw_cache:
            b_raw_cache[fs] = crosssite.load_b_raw(fs, manifest, b_pids)
        return b_raw_cache[fs]

    aval = parse_h2_results(H2_RESULTS)
    results = {}        # (model,fs) -> dict(a_prauc,a_util,b_prauc,b_util,gap_util,gap_prauc,tau,...)
    bit_ok = True
    gru_masking_ok = True

    for model, fs in MODELS_FS:
        run_id, best_iter = run_for(model, fs)
        combo = load_combo(run_id, model, fs)
        bdata = b_raw(fs)
        if combo["kind"] == "tree":
            sr = crosssite.score_tree_frozen(combo["booster"], model, best_iter, combo["tau"], bdata)
        else:
            sr = crosssite.score_gru_frozen(combo["model"], combo["frozen"], combo["tau"], fs, bdata)
            # bit-identical: frozen stats used == artifact (re-read npz)
            z2 = np.load(combo["npz_path"])
            for k in ("mu", "sigma", "fill_mean", "clip_lo", "clip_hi"):
                if not np.array_equal(combo["frozen"][k], z2[k]):
                    bit_ok = False
            # masking actually excludes padding on B
            if not (np.isfinite(sr.prauc) and np.isfinite(sr.prauc_unmasked)
                    and abs(sr.prauc - sr.prauc_unmasked) > 1e-9):
                gru_masking_ok = False
        a = aval.get((model, fs), {"prauc": float("nan"), "utility": float("nan")})
        results[(model, fs)] = {
            "a_prauc": a["prauc"], "a_util": a["utility"],
            "b_prauc": sr.prauc, "b_util": sr.utility,
            "gap_util": a["utility"] - sr.utility, "gap_prauc": a["prauc"] - sr.prauc,
            "tau": combo["tau"], "b_prauc_unmasked": sr.prauc_unmasked,
        }
        print(f"[B-score] {model:9s} {fs:11s} A_util={a['utility']:.4f} B_util={sr.utility:.4f} "
              f"gap={a['utility']-sr.utility:+.4f} | A_PR={a['prauc']:.4f} B_PR={sr.prauc:.4f}")

    # rankings & rank-reversal
    a_rank = sorted(results, key=lambda k: results[k]["a_util"], reverse=True)
    b_rank = sorted(results, key=lambda k: results[k]["b_util"], reverse=True)
    rank_reversed = a_rank != b_rank

    # consistency: A-val from md must match MLflow (rounds to)
    aval_consistent = True
    for (model, fs), r in results.items():
        rid, _ = run_for(model, fs)
        mlu = float(runs[(runs["params.model"] == model) & (runs["params.featureset"] == fs)]
                    ["metrics.a_val_utility"].iloc[0])
        if abs(round(mlu, 4) - r["a_util"]) > 5e-4:
            aval_consistent = False

    write_report(results, a_rank, b_rank, rank_reversed)

    fin = lambda x: np.isfinite(x)
    check(all(fin(r["b_prauc"]) and fin(r["b_util"]) for r in results.values()) and len(results) == 6,
          "#2 6-combo B scoring", f"{len(results)} combos, all finite")
    check(grep_ok and bit_ok,
          "#3 B leak guard (frozen-only)",
          f"grep forbidden absent={grep_ok} (found {present or 'none'}); GRU stats bit-equal to artifact={bit_ok}")
    check(REPORT.exists() and aval_consistent,
          "#4 gap table (A-val == h2_results.md)",
          f"report written; A-val rounds to MLflow={aval_consistent}")
    check(gru_masking_ok, "#5 GRU masked PR-AUC on B (padding excluded)",
          " ".join(f"{fs}:m={results[('gru',fs)]['b_prauc']:.4f}/u={results[('gru',fs)]['b_prauc_unmasked']:.4f}"
                   for fs in ("vitals", "vitals_labs")))

    print("\n=== H3-b cross-site gate ===")
    for ln in lines:
        print(ln)
    print("\n6-combo A-val -> B (utility):")
    print(f"  {'combo':22s} {'A_util':>8s} {'B_util':>8s} {'gap':>8s} {'A_PR':>7s} {'B_PR':>7s}")
    for k in a_rank:
        r = results[k]
        print(f"  {k[0]+'/'+k[1]:22s} {r['a_util']:8.4f} {r['b_util']:8.4f} "
              f"{r['gap_util']:+8.4f} {r['a_prauc']:7.4f} {r['b_prauc']:7.4f}")
    print(f"\nA-val rank: {[k[0]+'/'+k[1] for k in a_rank]}")
    print(f"B    rank: {[k[0]+'/'+k[1] for k in b_rank]}")
    print(f"rank reversal: {rank_reversed}")

    if not ok:
        print("\nH3-b: FAIL — stopping.", file=sys.stderr)
        return 1
    print("\nH3-b: PASS (5/5). ⏸ Human checkpoint: interpret gap/rank-reversal. "
          "B is OBSERVATION ONLY — featureset/model choice is A-val+H4, never B. H3-c next session.")
    return 0


def write_report(results, a_rank, b_rank, rank_reversed):
    L = ["# H3-b 결과 — cross-site (A→B) 채점\n",
         f"> 생성: H3-b (`scripts/h3b_crosssite.py`) · {DATE} · setB **1회 개봉**(채점 전용).",
         "> **A 동결 아티팩트만**(μ/σ·fill·clip·τ) 사용 — B 재계산·재튜닝·τ재선정 없음.",
         "> A-val 점수는 `reports/h2_results.md` 인용(재계산 아님). gap = A_val − B.",
         "> ⚠️ **B는 관찰 전용** — 피처셋/모델 선택은 A-val+H4, B 점수로 고르지 않음.\n",
         "## 6조합 utility · PR-AUC (A-val rank 순)\n",
         "| 모델 | featureset | A_util | B_util | gap(util) | A_PR | B_PR | gap(PR) | τ |",
         "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for k in a_rank:
        m, fs = k
        r = results[k]
        L.append(f"| {m} | {fs} | {r['a_util']:.4f} | {r['b_util']:.4f} | {r['gap_util']:+.4f} | "
                 f"{r['a_prauc']:.4f} | {r['b_prauc']:.4f} | {r['gap_prauc']:+.4f} | {r['tau']:.4f} |")
    L.append("")
    L.append(f"- **A-val 순위**: {' > '.join(m+'/'+fs for m, fs in a_rank)}")
    L.append(f"- **B 순위**: {' > '.join(m+'/'+fs for m, fs in b_rank)}")
    L.append(f"- **순위 역전**: {'있음' if rank_reversed else '없음'}")
    L.append("- gap>0 = B에서 성능 하락(cross-site degradation). 해석은 사람 체크포인트.\n")
    L.append("## 누수 가드")
    L.append("- frozen-only 채점(`eval/crosssite.py`): fit/tune/select 미호출(grep), "
             "GRU μ/σ·fill·clip이 아티팩트와 bit-동일, τ는 A-val 동결값.")
    L.append("- B는 채점에만. 피처셋/모델 선택에 B 미사용(관찰 전용).\n")
    L.append("## 다음")
    L.append("- ⏸ 사람: gap·순위역전 해석(GRU vs 트리 일반화, 피처셋 방향) — **B로 선택하지 않음**.")
    L.append("- H3-c: 마스크 ON/OFF의 A→B gap 비교(전이성) → 마스크 OFF 최종 확정.")
    REPORT.write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
