"""회귀 — validate._score 가 빈 입력에 crash 하지 않고 NaN 으로 degrade(D-retrain#2).

병적으로 작은 B(예: 1환자, holdout_frac<0.5 → round=0)면 b_holdout=[] 가 되어
_score(model, [], tau) 가 np.concatenate([]) 로 ValueError 를 던져 재학습 전체를 중단시켰다.
이제 빈 입력은 (nan, nan) 로 돌려 게이트를 깨지 않는다(B-holdout 은 정보성 지표, 승격 게이트는 A-val).
"""
from __future__ import annotations

import math

from sepsis.retrain import validate


def test_score_empty_returns_nan_without_touching_model():
    # 빈 data 는 gru.evaluate/np.concatenate 를 타기 전에 (nan, nan) 로 반환 → model 은 안 쓰임.
    util, pr = validate._score(model=None, data=[], tau=0.5)
    assert math.isnan(util) and math.isnan(pr)
