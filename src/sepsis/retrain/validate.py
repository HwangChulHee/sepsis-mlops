"""H4r-b — retrain validation (handoff H4r-b, DDD 결정 4·5).

Two checks: (a) B-holdout performance (new operational data, held out from B-retrain) and
(b) A-val NO-REGRESSION (new model vs old model, each scored with ITS OWN frozen stats).

★ IN-DISTRIBUTION ONLY: the retrained model has seen A+B, so there is NO unobserved third
distribution. B-holdout is in-distribution to B-retrain → this is NOT a cross-site
generalization claim (that needs a third site C, which we don't have). Flagged in output.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sklearn.metrics import average_precision_score

from sepsis.eval import crosssite, threshold
from sepsis.train import gru


def _score(model, data, tau):
    pl, pp, prauc, _ = gru.evaluate(model, data, batch_size=64)
    util = threshold.utility_at(pl, pp, tau)
    y = np.concatenate([np.asarray(l) for l in pl])
    p = np.concatenate([np.asarray(x) for x in pp])
    pr = float(average_precision_score(y, p)) if y.max() > 0 else float("nan")
    return util, pr


@dataclass
class ValidationResult:
    bholdout_util: float
    bholdout_prauc: float
    new_aval_util: float
    old_aval_util: float
    new_aval_prauc: float
    old_aval_prauc: float
    no_regression: bool
    cross_site_claim: bool = False
    distribution: str = ("in-distribution (model trained on A+B; no unobserved 3rd site → "
                         "NOT a cross-site generalization claim; B-holdout is in-dist to B-retrain)")
    note: str = ""


def validate(retrain_result, old_bundle, *, eps: float = 0.02) -> ValidationResult:
    """B-holdout (new data) perf + A-val no-regression (new vs old, own stats each)."""
    rr = retrain_result
    # (a) B-holdout: new operational data, new model + new stats
    bh_util, bh_prauc = _score(rr.model, rr.bholdout_data, rr.tau)

    # (b) A-val no-regression: new model (NEW stats) vs old model (OLD stats) on the SAME A-val
    new_util, new_prauc = _score(rr.model, rr.aval_data, rr.tau)
    old_frozen = {"mu": old_bundle.mu, "sigma": old_bundle.sigma, "fill_mean": old_bundle.fill_mean,
                  "clip_lo": old_bundle.clip_lo, "clip_hi": old_bundle.clip_hi}
    aval_old = [(crosssite._gru_transform_frozen(raw, old_frozen), lab) for raw, lab in rr.aval_raw]
    old_util, old_prauc = _score(old_bundle.model, aval_old, old_bundle.tau)

    no_reg = new_util >= old_util - eps                 # new model doesn't lose A performance
    return ValidationResult(
        bholdout_util=bh_util, bholdout_prauc=bh_prauc,
        new_aval_util=new_util, old_aval_util=old_util,
        new_aval_prauc=new_prauc, old_aval_prauc=old_prauc,
        no_regression=bool(no_reg),
        note=(f"A-val no-regression: new util {new_util:.4f} vs old {old_util:.4f} "
              f"(eps {eps}) -> {'OK' if no_reg else 'REGRESSED'}. "
              f"B-holdout (new data, in-dist) util {bh_util:.4f}."))
