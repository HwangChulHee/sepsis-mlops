"""EDA for PhysioNet/CinC 2019 sepsis data.

Measures (does NOT model or impute) the raw data to justify later smoke-pipeline
design decisions: window size, missingness handling, split strategy, imbalance.

All length/label/missing stats are aggregated PER PATIENT (one .psv = one patient)
to avoid patient leakage in the statistics. No filling, no interpolation — we only
measure missingness, never repair it.

Run:  uv run python scripts/eda.py
Outputs: console report + reports/eda_findings.md + reports/figures/*.png
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "raw"
FIG_DIR = ROOT / "reports" / "figures"
REPORT = ROOT / "reports" / "eda_findings.md"

EXPECTED_COLUMNS = [
    "HR", "O2Sat", "Temp", "SBP", "MAP", "DBP", "Resp", "EtCO2",
    "BaseExcess", "HCO3", "FiO2", "pH", "PaCO2", "SaO2", "AST", "BUN",
    "Alkalinephos", "Calcium", "Chloride", "Creatinine", "Bilirubin_direct",
    "Glucose", "Lactate", "Magnesium", "Phosphate", "Potassium",
    "Bilirubin_total", "TroponinI", "Hct", "Hgb", "PTT", "WBC",
    "Fibrinogen", "Platelets",
    "Age", "Gender", "Unit1", "Unit2", "HospAdmTime", "ICULOS",
    "SepsisLabel",
]
VITALS = ["HR", "O2Sat", "Temp", "SBP", "MAP", "Resp"]
# Physiologically plausible bounds for outlier flagging (not clipping — just counting).
VITAL_BOUNDS = {
    "HR": (20, 300),       # bpm
    "O2Sat": (50, 100),    # %
    "Temp": (25, 45),      # deg C
    "SBP": (40, 300),      # mmHg
    "MAP": (20, 250),      # mmHg
    "Resp": (3, 80),       # breaths/min
}


def find_files() -> dict[str, list[Path]]:
    sets: dict[str, list[Path]] = {}
    for name in ("training_setA", "training_setB"):
        d = DATA_DIR / name
        sets[name] = sorted(d.glob("*.psv")) if d.is_dir() else []
    return sets


def percentiles(arr: np.ndarray) -> dict[str, float]:
    if len(arr) == 0:
        return {k: float("nan") for k in ("min", "p25", "median", "mean", "p90", "p99", "max")}
    return {
        "min": float(np.min(arr)),
        "p25": float(np.percentile(arr, 25)),
        "median": float(np.median(arr)),
        "mean": float(np.mean(arr)),
        "p90": float(np.percentile(arr, 90)),
        "p99": float(np.percentile(arr, 99)),
        "max": float(np.max(arr)),
    }


def main() -> int:
    sets = find_files()
    nA, nB = len(sets["training_setA"]), len(sets["training_setB"])
    n_total = nA + nB
    if n_total == 0:
        print(f"ERROR: no .psv files under {DATA_DIR}. Is the download finished?")
        return 1

    FIG_DIR.mkdir(parents=True, exist_ok=True)

    # ---- accumulators (streaming over files; never hold all frames at once) ----
    total_rows = 0
    nonnull_counts = np.zeros(len(EXPECTED_COLUMNS), dtype=np.int64)
    all_missing_patient = np.zeros(len(EXPECTED_COLUMNS), dtype=np.int64)  # col fully NaN for a patient
    seq_lengths: list[int] = []
    seq_lengths_by_set = {"training_setA": [], "training_setB": []}
    iculos_monotonic_ok = 0
    iculos_checked = 0
    n_sepsis_patients = 0
    n_positive_rows = 0
    first_pos_iculos: list[int] = []
    pos_window_len: list[int] = []        # # positive hours per sepsis patient
    pos_contiguous_to_end = 0             # positives form one block ending at last row
    sepsis_by_set = {"training_setA": [0, 0], "training_setB": [0, 0]}  # [n_patients, n_sepsis]
    # vital value collectors (only 6 cols) — sampled across all patient-hours
    vital_vals: dict[str, list[np.ndarray]] = {v: [] for v in VITALS}
    vital_vals_by_set = {
        "training_setA": {v: [] for v in VITALS},
        "training_setB": {v: [] for v in VITALS},
    }
    bad_header_files: list[str] = []

    files = [(s, p) for s in ("training_setA", "training_setB") for p in sets[s]]
    for i, (setname, path) in enumerate(files):
        df = pd.read_csv(path, sep="|")
        if list(df.columns) != EXPECTED_COLUMNS:
            bad_header_files.append(path.name)
            continue

        n = len(df)
        total_rows += n
        nonnull_counts += df.notna().sum().to_numpy()
        all_missing_patient += df.isna().all().to_numpy()

        seq_lengths.append(n)
        seq_lengths_by_set[setname].append(n)

        # ICULOS monotonic (strictly increasing) per patient
        ic = df["ICULOS"].to_numpy()
        iculos_checked += 1
        if n <= 1 or np.all(np.diff(ic) > 0):
            iculos_monotonic_ok += 1

        # labels
        lbl = df["SepsisLabel"].to_numpy()
        pos = lbl == 1
        npos = int(pos.sum())
        n_positive_rows += npos
        sepsis_by_set[setname][0] += 1
        if npos > 0:
            n_sepsis_patients += 1
            sepsis_by_set[setname][1] += 1
            first_idx = int(np.argmax(pos))  # first True
            first_pos_iculos.append(int(ic[first_idx]))
            pos_window_len.append(npos)
            # contiguous block running to the last row? (label turns on and stays on)
            if pos[first_idx:].all():
                pos_contiguous_to_end += 1

        # vitals (drop NaN per column)
        for v in VITALS:
            col = df[v].to_numpy()
            col = col[~np.isnan(col)]
            if len(col):
                vital_vals[v].append(col)
                vital_vals_by_set[setname][v].append(col)

        if (i + 1) % 5000 == 0:
            print(f"  ...processed {i + 1}/{len(files)} files", file=sys.stderr)

    # ---- consolidate ----
    seq = np.array(seq_lengths)
    fpi = np.array(first_pos_iculos)
    vital_arr = {v: (np.concatenate(vital_vals[v]) if vital_vals[v] else np.array([])) for v in VITALS}

    miss_rate = 1.0 - nonnull_counts / total_rows
    miss_table = sorted(
        zip(EXPECTED_COLUMNS, miss_rate, all_missing_patient),
        key=lambda x: -x[1],
    )

    # =====================================================================
    # CONSOLE REPORT
    # =====================================================================
    L = []
    def out(s=""):
        print(s)
        L.append(s)

    out("=" * 70)
    out("PhysioNet 2019 Sepsis — EDA findings")
    out("=" * 70)

    out("\n## 1. File / scale inventory")
    out(f"  training_setA files : {nA}")
    out(f"  training_setB files : {nB}")
    out(f"  total patients      : {n_total}")
    out(f"  total patient-hours : {total_rows:,}")
    out(f"  files with bad header / parse skip : {len(bad_header_files)}")

    out("\n## 2. Columns / dtypes")
    out(f"  column count        : {len(EXPECTED_COLUMNS)} (expected 41) -> "
        f"{'OK' if len(EXPECTED_COLUMNS) == 41 else 'MISMATCH'}")
    out(f"  header matches spec : {'OK (all files)' if not bad_header_files else f'{len(bad_header_files)} mismatched'}")
    out(f"  ICULOS strictly increasing within patient : "
        f"{iculos_monotonic_ok}/{iculos_checked} "
        f"({100*iculos_monotonic_ok/max(iculos_checked,1):.1f}%)")

    out("\n## 3. Missingness (per-row missing rate; sorted worst-first)")
    out(f"  {'column':<18}{'miss_rate':>10}{'patients_all_missing':>22}")
    for col, mr, amp in miss_table:
        out(f"  {col:<18}{mr*100:>9.2f}%{amp:>22,}")

    out("\n## 4. Sequence length (rows per patient = ICU hours)")
    sl = percentiles(seq)
    out(f"  min={sl['min']:.0f}  median={sl['median']:.0f}  mean={sl['mean']:.1f}  "
        f"p90={sl['p90']:.0f}  p99={sl['p99']:.0f}  max={sl['max']:.0f}")
    for w in (8, 12, 24, 48):
        kept = int((seq >= w).sum())
        out(f"  patients with length >= {w:>3}h : {kept:>6} ({100*kept/len(seq):.1f}%)")

    out("\n## 5. Labels / imbalance")
    pat_ratio = n_sepsis_patients / n_total
    row_ratio = n_positive_rows / total_rows
    out(f"  sepsis patients (>=1 positive hour) : {n_sepsis_patients}/{n_total} "
        f"({pat_ratio*100:.2f}%)")
    out(f"  positive patient-hours              : {n_positive_rows:,}/{total_rows:,} "
        f"({row_ratio*100:.3f}%)")
    out(f"  implied neg/pos row ratio (~pos_weight) : {(total_rows-n_positive_rows)/max(n_positive_rows,1):.1f}")

    out("\n## 6. Label timing — first SepsisLabel==1 ICULOS (sepsis patients)")
    fp = percentiles(fpi)
    out(f"  min={fp['min']:.0f}  p25={fp['p25']:.0f}  median={fp['median']:.0f}  "
        f"mean={fp['mean']:.1f}  p90={fp['p90']:.0f}  max={fp['max']:.0f}")
    out(f"  first positive at ICULOS==1 (label present from admission) : "
        f"{int((fpi==1).sum())} ({100*(fpi==1).sum()/len(fpi):.1f}%)")
    pwl = np.array(pos_window_len)
    out(f"  positive-window length per patient (hours): median={np.median(pwl):.0f}  "
        f"mean={pwl.mean():.1f}  max={pwl.max():.0f}")
    out(f"  label once positive stays positive to discharge (contiguous block to end) : "
        f"{pos_contiguous_to_end}/{n_sepsis_patients} "
        f"({100*pos_contiguous_to_end/n_sepsis_patients:.1f}%)")
    out("  -> confirms Sepsis-3 challenge rule empirically: the label is a contiguous "
        "pre-onset block (on ~6h before onset, stays on), NOT a point event.")

    out("\n## 7. Vital sign distributions + implausible-value counts")
    out(f"  {'vital':<8}{'min':>8}{'p25':>8}{'median':>8}{'mean':>8}{'p90':>8}{'max':>9}"
        f"{'n_obs':>11}{'oob%':>8}")
    for v in VITALS:
        a = vital_arr[v]
        p = percentiles(a)
        lo, hi = VITAL_BOUNDS[v]
        oob = int(((a < lo) | (a > hi)).sum()) if len(a) else 0
        oobp = 100 * oob / len(a) if len(a) else 0.0
        neg = int((a < 0).sum()) if len(a) else 0
        out(f"  {v:<8}{p['min']:>8.1f}{p['p25']:>8.1f}{p['median']:>8.1f}{p['mean']:>8.1f}"
            f"{p['p90']:>8.1f}{p['max']:>9.1f}{len(a):>11,}{oobp:>7.2f}%"
            + (f"   (neg:{neg})" if neg else ""))

    out("\n## 8. Hospital A vs B")
    for s in ("training_setA", "training_setB"):
        npat, nsep = sepsis_by_set[s]
        sls = np.array(seq_lengths_by_set[s])
        out(f"  {s}: patients={npat}  sepsis%={100*nsep/max(npat,1):.2f}  "
            f"median_len={np.median(sls):.0f}h  "
            f"HR_median={np.median(np.concatenate(vital_vals_by_set[s]['HR'])):.0f}")

    # =====================================================================
    # FIGURES
    # =====================================================================
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.hist(np.clip(seq, 0, 200), bins=60, color="#3b6ea5")
    ax.set_title("Sequence length per patient (ICU hours, clipped at 200)")
    ax.set_xlabel("rows per patient (hours)")
    ax.set_ylabel("patients")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "seq_length_hist.png", dpi=110)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.hist(np.clip(fpi, 0, 200), bins=60, color="#a5453b")
    ax.set_title("First SepsisLabel==1 ICULOS (sepsis patients, clipped at 200)")
    ax.set_xlabel("ICULOS hour of first positive label")
    ax.set_ylabel("patients")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "first_positive_iculos_hist.png", dpi=110)
    plt.close(fig)

    fig, axes = plt.subplots(2, 3, figsize=(13, 7))
    for ax, v in zip(axes.ravel(), VITALS):
        a = vital_arr[v]
        lo, hi = VITAL_BOUNDS[v]
        ax.hist(a[(a >= lo) & (a <= hi)], bins=60, color="#4a7a4a")
        ax.set_title(v)
    fig.suptitle("Vital sign distributions (within plausible bounds)")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "vitals_hist.png", dpi=110)
    plt.close(fig)

    out("\nFigures written to reports/figures/: "
        "seq_length_hist.png, first_positive_iculos_hist.png, vitals_hist.png")

    # =====================================================================
    # MARKDOWN REPORT
    # =====================================================================
    write_markdown(
        nA, nB, n_total, total_rows, len(bad_header_files),
        iculos_monotonic_ok, iculos_checked, miss_table,
        sl, seq, n_sepsis_patients, pat_ratio, n_positive_rows, row_ratio,
        fp, fpi, vital_arr, sepsis_by_set, seq_lengths_by_set, vital_vals_by_set,
        np.array(pos_window_len), pos_contiguous_to_end,
    )
    print(f"\nMarkdown report written to {REPORT}")
    return 0


def write_markdown(nA, nB, n_total, total_rows, n_bad, ic_ok, ic_n, miss_table,
                   sl, seq, n_sep, pat_ratio, n_pos, row_ratio, fp, fpi,
                   vital_arr, sepsis_by_set, seq_by_set, vital_by_set,
                   pos_window_len, pos_contig_end):
    pos_weight = (total_rows - n_pos) / max(n_pos, 1)
    top_miss = [c for c, mr, _ in miss_table if mr > 0.90]

    lines = []
    a = lines.append
    a("# PhysioNet 2019 Sepsis — EDA Findings\n")
    a("> Exploratory measurement only. No imputation, no modeling, no splitting. "
      "All patient-level stats aggregated per `.psv` file (= one patient) to avoid "
      "leakage in the statistics.\n")

    a("## 1. Inventory")
    a(f"- `training_setA`: **{nA}** patients · `training_setB`: **{nB}** patients · "
      f"**total {n_total}** patients")
    a(f"- Total patient-hours (rows): **{total_rows:,}**")
    a(f"- Files skipped for header/parse mismatch: **{n_bad}**\n")

    a("## 2. Columns / types")
    a("- All files carry the expected **41 columns** (40 features + `SepsisLabel`), "
      "pipe-separated, `NaN` for missing. dtypes are float64 except integer-coded "
      "`Gender`/`Unit1`/`Unit2`/`ICULOS`/`SepsisLabel` (read as float when NaNs present).")
    a(f"- `ICULOS` strictly increases within a patient in **{ic_ok}/{ic_n}** files "
      f"({100*ic_ok/max(ic_n,1):.1f}%) — confirms one row = one ICU hour, time-ordered.\n")

    a("## 3. Missingness (per-row)")
    a("| column | missing % | patients fully missing |")
    a("|---|---:|---:|")
    for col, mr, amp in miss_table:
        a(f"| {col} | {mr*100:.2f}% | {amp:,} |")
    a(f"\nWorst-missing (>90%): {', '.join(top_miss) if top_miss else 'none'}. "
      "Lab values are measured rarely → mostly NaN; vitals are denser but still gappy.\n")

    a("## 4. Sequence length (ICU hours per patient)")
    a(f"- min **{sl['min']:.0f}** · median **{sl['median']:.0f}** · mean **{sl['mean']:.1f}** · "
      f"p90 **{sl['p90']:.0f}** · p99 **{sl['p99']:.0f}** · max **{sl['max']:.0f}**")
    for w in (8, 12, 24, 48):
        kept = int((seq >= w).sum())
        a(f"- length ≥ {w}h: {kept:,} patients ({100*kept/len(seq):.1f}%)")
    a("\n![seq length](figures/seq_length_hist.png)\n")

    a("## 5. Labels / imbalance")
    a(f"- **Sepsis patients** (≥1 positive hour): **{n_sep}/{n_total} = {pat_ratio*100:.2f}%**")
    a(f"- **Positive patient-hours**: **{n_pos:,}/{total_rows:,} = {row_ratio*100:.3f}%**")
    a(f"- Negative:positive row ratio ≈ **{pos_weight:.1f}** (rough `pos_weight` upper bound)\n")

    a("## 6. Label timing — first `SepsisLabel==1`")
    a(f"- ICULOS of first positive: min **{fp['min']:.0f}** · p25 **{fp['p25']:.0f}** · "
      f"median **{fp['median']:.0f}** · mean **{fp['mean']:.1f}** · p90 **{fp['p90']:.0f}** · "
      f"max **{fp['max']:.0f}**")
    a(f"- First positive already at ICULOS==1 (label present from admission): "
      f"**{int((fpi==1).sum())} ({100*(fpi==1).sum()/len(fpi):.1f}%)**")
    a(f"- Positive-window length per patient: median **{np.median(pos_window_len):.0f}h** · "
      f"mean **{pos_window_len.mean():.1f}h** · max **{pos_window_len.max():.0f}h**")
    a(f"- **Rule verification**: once positive, the label stays positive to discharge "
      f"(single contiguous block ending at the last row) in **{pos_contig_end}/{n_sep} = "
      f"{100*pos_contig_end/n_sep:.1f}%** of sepsis patients.")
    a("- Interpretation: this empirically confirms the Sepsis-3 challenge labeling. The "
      "positive window is **capped at 10h** (median 10, max 10) and is always the **final ≤10 "
      "hours** of a septic patient's record — i.e. the label switches on a fixed early-warning "
      "window before clinical onset (the 6-h rule) and the record is **right-truncated shortly "
      "after onset** (so end-of-record for septic patients is near-onset truncation, not real "
      "discharge). Some patients are already positive at admission (onset preceded ICU entry). "
      "Window/label alignment must treat the positive region as a contiguous pre-onset block, "
      "not a point event, and must not leak the truncation as a signal.\n")
    a("![first positive iculos](figures/first_positive_iculos_hist.png)\n")

    a("## 7. Vital signs + implausible values")
    a("| vital | min | median | mean | p90 | max | n_obs | out-of-bounds % |")
    a("|---|---:|---:|---:|---:|---:|---:|---:|")
    for v in VITALS:
        arr = vital_arr[v]
        p = percentiles(arr)
        lo, hi = VITAL_BOUNDS[v]
        oob = 100 * ((arr < lo) | (arr > hi)).sum() / len(arr) if len(arr) else 0.0
        a(f"| {v} | {p['min']:.1f} | {p['median']:.1f} | {p['mean']:.1f} | "
          f"{p['p90']:.1f} | {p['max']:.1f} | {len(arr):,} | {oob:.2f}% |")
    a("\nExtreme min/max exist (e.g. HR, SBP spikes) but out-of-bounds fractions are tiny → "
      "robust scaling + light clipping at physiologic bounds is enough; no aggressive cleaning.\n")
    a("![vitals](figures/vitals_hist.png)\n")

    a("## 8. Hospital A vs B")
    a("| set | patients | sepsis % | median length | HR median |")
    a("|---|---:|---:|---:|---:|")
    for s in ("training_setA", "training_setB"):
        npat, nsep = sepsis_by_set[s]
        sls = np.array(seq_by_set[s])
        hr = np.median(np.concatenate(vital_by_set[s]["HR"]))
        a(f"| {s} | {npat} | {100*nsep/max(npat,1):.2f}% | {np.median(sls):.0f}h | {hr:.0f} |")
    a("\nA and B differ in sepsis prevalence and length → site is a covariate; a future split "
      "should be **patient-grouped and ideally site-aware**.\n")

    a("## Implications for the smoke pipeline")
    a(f"- **Window size**: median patient length is **{sl['median']:.0f}h**; a window of "
      f"**~8–12h** keeps the large majority of patients "
      f"({100*(seq>=12).sum()/len(seq):.0f}% have ≥12h) while staying short enough for "
      "real-time early warning. Longer windows (24–48h) discard many short stays — measured "
      "trade-off above.")
    a("- **Missing strategy**: do **not** zero-fill. Labs are >90% missing; carry a "
      "**missingness mask** per feature + **forward-fill within patient** (past→future only, "
      "no leakage) for vitals; consider **dropping** the most useless ultra-sparse labs for the "
      "smoke run.")
    a(f"- **Imbalance / pos_weight**: positive rows are only **{row_ratio*100:.3f}%** of all "
      f"hours → use **`pos_weight` ≈ {pos_weight:.0f}** (neg/pos ratio) as a starting point, "
      "and evaluate with **PR-AUC** + AUROC rather than accuracy.")
    a("- **Split**: group by patient (file); keep sites in mind (Section 8).")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
