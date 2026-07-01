"""H2-a gate — official utility implementation + validation (h2_handoff.md H2-a).

5 programmatic asserts; all PASS -> H2-a complete. Any FAIL -> stop & report.

    uv run python -m scripts.h2.h2a_utility_check

NOTE on the 14-row table: the handoff inlines expected values rounded to 2 decimals
(e.g. +0.89 = 8/9), so a literal ±1e-6 compare against the *displayed* table is
impossible. We anchor the ±1e-6 asserts to the EXACT official-definition values, and
additionally assert each exact value rounds to the table's 2-decimal display — so
PASS #3 checks both precision AND faithfulness to the inlined table.
"""

from __future__ import annotations

import sys

import numpy as np

from sepsis.eval import utility as U
from sepsis.util.progress import ProgressLogger

TOL = 1e-6

# EXACT expected per-timestep values (offset n = t - t_sepsis), from the official
# definition. The handoff's 14-row table is these rounded to 2 decimals.
EXPECTED = [
    (-13, U.U_FP, 0.0),     # < -12: too early, clipped to u_fp
    (-12, 0.0, 0.0),
    (-9, 0.5, 0.0),
    (-6, 1.0, 0.0),         # optimal: max reward
    (-5, 8 / 9, -2 / 9),
    (-4, 7 / 9, -4 / 9),
    (-3, 6 / 9, -6 / 9),
    (-2, 5 / 9, -8 / 9),
    (-1, 4 / 9, -10 / 9),
    (0, 3 / 9, -12 / 9),    # onset
    (1, 2 / 9, -14 / 9),
    (2, 1 / 9, -16 / 9),
    (3, 0.0, -18 / 9),      # window closes / max FN penalty
    (4, 0.0, 0.0),          # > +3: window closed
]
# the handoff's inlined 14-row table (2-decimal display), for the faithfulness check
TABLE_2DP = {
    -13: (-0.05, 0.0), -12: (0.0, 0.0), -9: (0.50, 0.0), -6: (1.00, 0.0),
    -5: (0.89, -0.22), -4: (0.78, -0.44), -3: (0.67, -0.67), -2: (0.56, -0.89),
    -1: (0.44, -1.11), 0: (0.33, -1.33), 1: (0.22, -1.56), 2: (0.11, -1.78),
    3: (0.0, -2.00), 4: (0.0, 0.0),
}


def synth_septic(t_sepsis: int, length: int) -> np.ndarray:
    """Septic patient: label turns on at (t_sepsis - 6) and stays on (EDA: monotone block)."""
    first_pos = t_sepsis + U.DT_OPTIMAL  # = t_sepsis - 6
    labels = np.zeros(length, dtype=np.int8)
    labels[first_pos:] = 1
    return labels


def main() -> int:
    lines: list[str] = []
    ok = True

    def check(cond: bool, label: str, detail: str) -> None:
        nonlocal ok
        if not cond:
            ok = False
        lines.append(f"[{'PASS' if cond else 'FAIL'}] {label}: {detail}")

    prog = ProgressLogger(5, "h2a", log_path="logs/h2_a.log")

    # cohort spanning edge cases: long septic, short septic, immediate-positive, non-septic
    cohort = [
        synth_septic(t_sepsis=20, length=40),
        synth_septic(t_sepsis=10, length=25),   # window start clamps at 0
        synth_septic(t_sepsis=6, length=30),    # first_pos=0 (immediate positive)
        np.zeros(30, dtype=np.int8),            # non-septic
    ]

    # --- #1 all-negative -> U_norm == 0.0 ---
    res_in = U.normalized_utility([(lab, U.inaction_predictions(lab)) for lab in cohort])
    check(abs(res_in.u_normalized - 0.0) <= TOL, "#1 all-negative -> 0.0",
          f"U_norm={res_in.u_normalized:.3e} "
          f"(U_obs={res_in.u_observed:.4f}==U_inaction={res_in.u_inaction:.4f})")
    prog.update(1)

    # --- #2 best_predictions -> U_norm == 1.0 ---
    res_best = U.normalized_utility([(lab, U.best_predictions(lab)) for lab in cohort])
    check(abs(res_best.u_normalized - 1.0) <= TOL, "#2 best_predictions -> 1.0",
          f"U_norm={res_best.u_normalized:.6f} "
          f"(U_best={res_best.u_best:.4f}, U_inaction={res_best.u_inaction:.4f})")
    prog.update(2)

    # --- #3 14-row table (exact ±1e-6, AND rounds to inlined 2dp table) ---
    t_sepsis, length = 20, 40
    labels = synth_septic(t_sepsis, length)
    u_tp = U.utility_per_timestep(labels, np.ones(length, dtype=np.int8))   # predict 1 everywhere
    u_fn = U.utility_per_timestep(labels, np.zeros(length, dtype=np.int8))  # predict 0 everywhere
    worst_exact = 0.0
    table_ok = True
    bad = []
    for n, exp_tp, exp_fn in EXPECTED:
        t = t_sepsis + n
        got_tp, got_fn = float(u_tp[t]), float(u_fn[t])
        d_tp, d_fn = abs(got_tp - exp_tp), abs(got_fn - exp_fn)
        worst_exact = max(worst_exact, d_tp, d_fn)
        tab_tp, tab_fn = TABLE_2DP[n]
        if round(got_tp, 2) != tab_tp or round(got_fn, 2) != tab_fn:
            table_ok = False
        if d_tp > TOL or d_fn > TOL:
            bad.append(f"n={n}: TP {got_tp:.6f}!={exp_tp:.6f} or FN {got_fn:.6f}!={exp_fn:.6f}")
    check(worst_exact <= TOL and table_ok and not bad, "#3 14-row table",
          f"max|Δ_exact|={worst_exact:.2e} (<=1e-6), rounds-to-2dp-table={table_ok}"
          + (f"; MISMATCH {bad}" if bad else ""))
    prog.update(3)

    # --- #4 t_sepsis derivation: first_pos k -> t_sepsis==k+6, +1.0 at n=-6 (=index k) ---
    deriv_ok = True
    details = []
    for k in (0, 1, 7, 14, 50):
        L = k + 20
        lab = np.zeros(L, dtype=np.int8)
        lab[k:] = 1
        ts = U.onset_index(lab)
        u1 = U.utility_per_timestep(lab, np.ones(L, dtype=np.int8))
        peak_at_k = abs(u1[k] - 1.0) <= TOL          # first positive index is the optimal (+1.0)
        if ts != k + 6 or not peak_at_k:
            deriv_ok = False
            details.append(f"k={k}: t_sepsis={ts}(exp {k+6}), U_TP[k]={u1[k]:.6f}")
    check(deriv_ok, "#4 t_sepsis = first_pos + 6",
          "k in {0,1,7,14,50}: t_sepsis==k+6 AND +1.0 at first-positive index"
          + (f"; FAIL {details}" if details else ""))
    prog.update(4)

    # --- #5 constants/slopes match official evaluate_sepsis_score.py ---
    const_checks = {
        "dt_early": (U.DT_EARLY, -12), "dt_optimal": (U.DT_OPTIMAL, -6),
        "dt_late": (U.DT_LATE, 3), "u_fp": (U.U_FP, -0.05), "u_tn": (U.U_TN, 0.0),
        "max_u_tp": (U.MAX_U_TP, 1.0), "min_u_fn": (U.MIN_U_FN, -2.0),
        "m1": (U.M1, 1 / 6), "m2": (U.M2, -1 / 9), "m3": (U.M3, -2 / 9),
    }
    const_bad = [f"{k}={v}!={exp}" for k, (v, exp) in const_checks.items() if abs(v - exp) > 1e-12]
    check(not const_bad, "#5 constants == official",
          "dt(-12/-6/3), u_fp=-0.05, u_tn=0, m1/m2/m3=1/6,-1/9,-2/9"
          + (f"; BAD {const_bad}" if const_bad else ""))
    prog.update(5)

    print("\n=== H2-a utility gate ===")
    for ln in lines:
        print(ln)
    prog.done("PASS 5/5" if ok else "FAILED")

    if not ok:
        print("\nH2-a: FAIL — stopping.", file=sys.stderr)
        return 1
    print("\nH2-a: PASS (5/5). ⏸ Report to human; H2-b is the next session.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
