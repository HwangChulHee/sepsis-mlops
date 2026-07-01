"""회귀 — select_baseline 은 tree 모델 결과 누락 시 명확히 실패(D-train 저심각).

빈 dict 에 max() 를 부르면 불명확한 `max() arg is an empty sequence` 가 나던 것을,
무엇이 없는지 밝히는 ValueError 로 바꾼다.
"""
from __future__ import annotations

import pandas as pd
import pytest

from sepsis.train import select as select_mod


def test_missing_tree_model_raises_clear_error():
    # lightgbm 결과가 하나도 없는 df — 과거엔 max({}) 로 모호하게 터졌다.
    df = pd.DataFrame([
        {"model": "xgboost", "featureset": "vitals", "utility": 0.4},
        {"model": "xgboost", "featureset": "vitals_labs", "utility": 0.39},
    ])
    with pytest.raises(ValueError, match="lightgbm"):
        select_mod.select_baseline(df)


def test_both_models_present_selects_winner():
    df = pd.DataFrame([
        {"model": "xgboost", "featureset": "vitals", "utility": 0.30},
        {"model": "lightgbm", "featureset": "vitals", "utility": 0.42},
    ])
    choice = select_mod.select_baseline(df)
    assert choice.model == "lightgbm"
