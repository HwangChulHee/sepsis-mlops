"""H1-a runner — build the raw NaN-preserving cache and run the 5 PASS asserts.

    uv run python -m scripts.data.build_cache              # full build (40,336 patients)
    uv run python -m scripts.data.build_cache --limit 500  # quick wiring check
    uv run python -m scripts.data.build_cache --verify-only

STOP on any FAIL: exits non-zero and does not proceed to H1-b.
"""

from __future__ import annotations

import argparse
import sys

from sepsis import config as C
from sepsis.data import cache as cache_mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="cap patients (smoke wiring only)")
    ap.add_argument("--verify-only", action="store_true", help="skip build, verify existing cache")
    ap.add_argument("--tol-pct", type=float, default=0.5, help="lab missing%% tolerance vs EDA")
    cfg = ap.parse_args()

    if not cfg.verify_only:
        cache_mod.build_cache(limit=cfg.limit)

    ok, lines, stats = cache_mod.verify_cache(tol_pct=cfg.tol_pct)

    print("\n=== H1-a PASS gate ===")
    for line in lines:
        print(line)

    print("\n=== cache summary ===")
    print(f"patients      : {stats.n_patients} (per-site {stats.per_site})")
    print(f"feature cols  : {stats.n_feature_cols}  {stats.feature_names}")
    print(f"total rows    : {stats.total_rows:,}")
    print(f"positive pts  : {stats.total_positive_patients}")
    print("lab missing %% (cache vs EDA):")
    for lab in C.LABS_9:
        print(f"  {lab:<12} {stats.lab_missing_pct[lab]:6.2f}%  (EDA {C.EDA_LAB_MISSING_PCT[lab]:.2f}%)")

    if cfg.limit is not None and not cfg.verify_only:
        print("\n[note] --limit run: asserts #1/#4 reference the FULL dataset and will "
              "FAIL on a partial cache. Use a full build for the real gate.")

    if not ok:
        print("\nH1-a: FAIL — stopping. Do not proceed to H1-b.", file=sys.stderr)
        return 1
    print("\nH1-a: PASS (5/5). Cache ready for H1-b (next session).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
