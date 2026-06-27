"""H1 configuration — columns, feature sets, paths, EDA reference stats.

Single source of truth for the column definitions in h1_handoff.md (v3, §0).
Decision references: h1_decisions.md 결정 1 (features) / 결정 8 (cache).
"""

from __future__ import annotations

from pathlib import Path

# repo root: src/sepsis/config.py -> parents[2]
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "raw"
CACHE_DIR = ROOT / "data" / "cache" / "h1"  # under data/ (git-ignored)

SITES = ("training_setA", "training_setB")
SITE_COUNTS = {"training_setA": 20336, "training_setB": 20000}
N_PATIENTS = 40336  # setA 20,336 + setB 20,000

# --- column definitions (PhysioNet 2019 .psv header, verified) ---
VITALS_7 = ["HR", "O2Sat", "Temp", "SBP", "MAP", "DBP", "Resp"]  # EtCO2 excluded from model
DEMOGRAPHICS = ["Age", "Gender"]
LABS_9 = ["WBC", "BUN", "Platelets", "Lactate", "Creatinine", "Glucose", "PTT", "HCO3", "Calcium"]
ETCO2 = "EtCO2"  # dead channel; kept in cache only (추후 재포함 여지)
LABEL = "SepsisLabel"

# cache feature order: vitals7 + demo2 + labs9 + EtCO2 = 19.
# NOTE the order makes model feature sets prefix-slices of the cache (EtCO2 last):
#   FEATURESET_VITALS      = indices 0..8   (9)
#   FEATURESET_VITALS_LABS = indices 0..17  (18)  -> excludes EtCO2 at index 18
CACHE_FEATURES = VITALS_7 + DEMOGRAPHICS + LABS_9 + [ETCO2]
assert len(CACHE_FEATURES) == 19, "cache must hold exactly 19 feature columns"

# model input feature sets (결정 1) — EtCO2 NOT included in either
FEATURESET_VITALS = VITALS_7 + DEMOGRAPHICS            # 9
FEATURESET_VITALS_LABS = VITALS_7 + DEMOGRAPHICS + LABS_9  # 18

# excluded everywhere (model + cache). The remaining 17 labs are simply not loaded.
EXCLUDED_NONFEATURES = ["ICULOS", "Unit1", "Unit2", "HospAdmTime"]

# EDA reference row-level missing % (reports/eda_findings.md §3) — for cache assert #4
EDA_LAB_MISSING_PCT = {
    "WBC": 93.59,
    "BUN": 93.13,
    "Platelets": 94.06,
    "Lactate": 97.33,
    "Creatinine": 93.90,
    "Glucose": 82.89,
    "PTT": 97.06,
    "HCO3": 95.81,
    "Calcium": 94.12,
}
