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

# 런타임 산출물(dev sqlite DB·캐시)은 루트를 어지럽히지 않게 var/ 아래로 모은다(git-ignored).
# 프로덕션은 이 dev 기본값 대신 env(CONSOLE_AUDIT_DB_URL 등)를 주입한다.
VAR_DIR = ROOT / "var"


def mlflow_uri() -> str:
    """dev MLflow tracking sqlite URI(var/mlflow.db). 접근 시 var/ 를 보장(신규 클론 안전)."""
    VAR_DIR.mkdir(exist_ok=True)
    return f"sqlite:///{VAR_DIR / 'mlflow.db'}"


def audit_uri() -> str:
    """dev 감사 sqlite URI(var/console_audit.db). 프로덕션은 CONSOLE_AUDIT_DB_URL 로 대체."""
    VAR_DIR.mkdir(exist_ok=True)
    return f"sqlite:///{VAR_DIR / 'console_audit.db'}"

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

# --- H1-b: model / transform constants ---
# Causal streaming model (결정 4): GRU must be unidirectional so right-padding +
# loss masking is leak-free. The model lives in re-smoke/H2; this is the binding flag.
GRU_BIDIRECTIONAL = False
LOOKBACK = 8  # tree per-timestep lookback window in hours (결정 6)
TREE_STATS = ["last", "mean", "min", "max", "delta", "range", "variance"]

# Fixed physiological clip bounds (결정 3) — DATA-INDEPENDENT (no leak), light trim.
# Keyed by the 18 model features (EtCO2 excluded; not a model input).
CLIP_BOUNDS = {
    "HR": (0.0, 300.0), "O2Sat": (0.0, 100.0), "Temp": (20.0, 45.0),
    "SBP": (0.0, 300.0), "MAP": (0.0, 250.0), "DBP": (0.0, 250.0), "Resp": (0.0, 80.0),
    "Age": (0.0, 120.0), "Gender": (0.0, 1.0),
    "WBC": (0.0, 200.0), "BUN": (0.0, 300.0), "Platelets": (0.0, 2000.0),
    "Lactate": (0.0, 40.0), "Creatinine": (0.0, 50.0), "Glucose": (0.0, 2000.0),
    "PTT": (0.0, 250.0), "HCO3": (0.0, 60.0), "Calcium": (0.0, 20.0),
}

FEATURESETS = {"vitals": FEATURESET_VITALS, "vitals_labs": FEATURESET_VITALS_LABS}


def featureset_columns(name: str) -> list[str]:
    if name not in FEATURESETS:
        raise ValueError(f"unknown featureset {name!r}; choose from {list(FEATURESETS)}")
    return list(FEATURESETS[name])


def featureset_indices(name: str) -> list[int]:
    """Column indices into CACHE_FEATURES for a model feature set (EtCO2 excluded)."""
    return [CACHE_FEATURES.index(c) for c in featureset_columns(name)]


# EDA reference row-level missing % (docs/reports/eda_findings.md §3) — for cache assert #4
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
