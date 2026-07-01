"""H4d-a gate — drift engine (reference + distance + synthetic calibration).

5 programmatic asserts; all PASS -> H4d-a done. Any FAIL -> stop & report.

    uv run python -m scripts.h4.h4d_a_smoke
"""

from __future__ import annotations

import ast
import sys

import numpy as np

from sepsis import config as C
from sepsis.data import cache as cache_mod
from sepsis.data import split as split_mod
from sepsis.drift import distance as D
from sepsis.drift import reference as R
from sepsis.drift import synthetic as S

FS = "vitals"
ALPHA = 0.05


def main() -> int:
    lines, ok = [], True

    def check(cond, label, detail):
        nonlocal ok
        if not cond:
            ok = False
        lines.append(f"[{'PASS' if cond else 'FAIL'}] {label}: {detail}")

    ref = R.build_reference(FS)
    path = R.save_reference(ref)
    ref = R.load_reference(path)                       # round-trip (frozen, reproducible)

    # context: patients vs total timesteps (unit check)
    man = cache_mod.load_manifest()
    n_a_train = len(split_mod.split_cross_site(man, val_frac=0.2, seed=42)["A_train"])
    total_timesteps = int(man["n_timesteps"].sum())

    # --- #1 reference RAW + missing, per-patient(last) unit, not normalized/timestep ---
    cols = C.featureset_columns(FS)
    hr = ref.summary[:, cols.index("HR")]
    hr_mean = float(np.nanmean(hr))                    # raw HR ~ 80s; z-scored would be ~0
    raw_not_norm = hr_mean > 20 and float(np.nanstd(hr)) > 3
    unit_ok = (ref.unit == "patient_last" and ref.summary.shape[0] == ref.n_patients == n_a_train
               and ref.n_patients < total_timesteps / 10)
    miss_ok = ref.missing_rate.shape == (len(cols),) and np.all(np.isfinite(ref.missing_rate))
    check(raw_not_norm and unit_ok and miss_ok,
          "#1 reference RAW + missing, per-patient(last) unit",
          f"n_patients={ref.n_patients}(==A_train, vs {total_timesteps} timesteps), unit={ref.unit}, "
          f"HR mean={hr_mean:.1f}(raw, not z), missing_rate{np.round(ref.missing_rate,3)}")

    # --- #2 distance computed (patient unit) + unit-match assert ---
    fd = D.feature_distances(ref, ref.summary)          # ref vs itself -> ~0
    self_max = max(abs(f["value"]) for f in fd if np.isfinite(f["value"]))
    raised = False
    try:
        D.feature_distances(ref, np.zeros((10, len(cols) + 1)))   # wrong F -> must raise
    except ValueError:
        raised = True
    check(self_max < 1e-9 and raised,
          "#2 distance (patient unit) + unit-match assert",
          f"ref-vs-ref max value-dist={self_max:.2e}(~0), unit-mismatch raised={raised}")

    # --- calibrate thresholds empirically (no analytic alpha) ---
    thr = S.calibrate(ref, alpha=ALPHA, window_n=500, n_trials=400, seed=0)

    # --- #3 synthetic injection detected ---
    rng = np.random.default_rng(7)
    cur = S.bootstrap(ref.summary, 500, rng)
    hr_i, temp_i = cols.index("HR"), cols.index("Temp")
    hr_std = float(np.nanstd(ref.summary[:, hr_i]))
    shifted = S.inject_mean_shift(cur, hr_i, delta=hr_std)          # 1-SD HR shift
    missed = S.inject_missing_increase(cur, temp_i, extra_rate=0.3, rng=rng)
    det_shift = {f["feature"]: f for f in S.detect(ref, shifted, thr)}
    det_miss = {f["feature"]: f for f in S.detect(ref, missed, thr)}
    inj_ok = det_shift["HR"]["value_drift"] and det_miss["Temp"]["missing_drift"]
    check(inj_ok, "#3 synthetic injection detected",
          f"HR mean-shift({hr_std:.1f}) value={det_shift['HR']['value']:.3f}>thr "
          f"{det_shift['HR']['value_thr']:.3f}; Temp +0.3 missing_js={det_miss['Temp']['missing_js']:.3f}>"
          f"thr {det_miss['Temp']['missing_thr']:.3f}")

    # --- #4 false-alarm rate ~ alpha on fresh H0 ---
    far = S.false_alarm_rate(ref, thr, n_trials=400, seed=1)
    fpr_ok = 0.02 <= far["value_fpr"] <= 0.09           # calibrated at alpha=0.05
    check(fpr_ok, "#4 false-alarm rate ≈ α (empirical)",
          f"value_fpr={far['value_fpr']:.3f} (target {ALPHA}); missing_fpr={far['missing_fpr']:.3f}; "
          f"any_fpr={far['any_fpr']:.3f} over {far['n_comparisons']} comparisons")

    # --- #5 no KS/analytic-α/Bonferroni (distance metrics only) ---
    forbidden = {"ks_2samp", "kstest", "ks_1samp", "multipletests", "bonferroni"}
    used = set()
    drift_files = sorted((C.ROOT / "src/sepsis/drift").glob("*.py"))
    for f in drift_files:
        tree = ast.parse(f.read_text())
        used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        used |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    present = sorted(forbidden & used)
    check(not present, "#5 distance-only (no KS/α/Bonferroni)",
          f"scanned {len(drift_files)} drift files; forbidden={present or 'none'}")

    print("\n=== H4d-a drift engine gate ===")
    for ln in lines:
        print(ln)
    if not ok:
        print("\nH4d-a: FAIL — stopping.", file=sys.stderr)
        return 1
    print("\nH4d-a: PASS (5/5). H4d-b (Evidently + window + watch + Grafana) is the next session.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
