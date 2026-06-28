"""H2-b — tree models (XGBoost, LightGBM). NaN-native, scale_pos_weight fixed.

Input = per-timestep lookback summaries (features.py), NaN as-is (handled natively).
scale_pos_weight is FIXED to the H1-derived A-train pos_weight (not tuned — DDD 경미).
Early stopping on A-val (aucpr for XGB, auc for LGBM — smooth stop signals; the model
SELECTION objective is A-val utility, computed separately).
"""

from __future__ import annotations

import numpy as np

MAX_ESTIMATORS = 400
EARLY_STOP = 30


def train(model_name: str, X_tr: np.ndarray, y_tr: np.ndarray,
          X_va: np.ndarray, y_va: np.ndarray, hp: dict, *,
          scale_pos_weight: float, seed: int):
    """Train one tree model with early stopping on (X_va, y_va). Returns fitted model."""
    if model_name == "xgboost":
        import xgboost as xgb
        m = xgb.XGBClassifier(
            tree_method="hist", n_estimators=MAX_ESTIMATORS,
            learning_rate=hp["learning_rate"], max_depth=hp["max_depth"],
            subsample=hp["subsample"], colsample_bytree=hp["colsample_bytree"],
            reg_lambda=hp["reg_lambda"], scale_pos_weight=scale_pos_weight,
            eval_metric="aucpr", early_stopping_rounds=EARLY_STOP,
            random_state=seed, n_jobs=-1)
        m.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=False)
        return m
    if model_name == "lightgbm":
        import lightgbm as lgb
        m = lgb.LGBMClassifier(
            n_estimators=MAX_ESTIMATORS, learning_rate=hp["learning_rate"],
            num_leaves=hp["num_leaves"], subsample=hp["subsample"], subsample_freq=1,
            colsample_bytree=hp["colsample_bytree"], reg_lambda=hp["reg_lambda"],
            scale_pos_weight=scale_pos_weight, random_state=seed, n_jobs=-1, verbosity=-1)
        m.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], eval_metric="auc",
              callbacks=[lgb.early_stopping(EARLY_STOP, verbose=False)])
        return m
    raise ValueError(f"unknown model {model_name!r}")


def predict_proba(model, X: np.ndarray) -> np.ndarray:
    return model.predict_proba(X)[:, 1].astype(np.float64)


def best_iteration(model_name: str, model) -> int:
    if model_name == "xgboost":
        return int(getattr(model, "best_iteration", -1) or -1)
    return int(getattr(model, "best_iteration_", -1) or -1)
