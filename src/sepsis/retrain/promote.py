"""H4r-a — watch->action promotion (handoff H4r-a, DDD 결정 1·2).

Turns drift watch signals (drift/watch.py DRIFT_STATE per feature + DATASET_DRIFT_SHARE,
here in their structured detection form) into an ACTION = "investigate" RECOMMENDATION.
DRIFT-DRIVEN: the decision uses drift share + PERSISTENCE across analysis cycles, never
performance (performance is delayed/umbrella-biased — auxiliary only). This module returns
a recommendation ONLY — it NEVER triggers retraining or deployment (human-in-the-loop:
no import/call of pipeline/deploy/train). watch_state == 1 means "drift observed", not
an alarm; promotion to "investigate" still hands the decision to a human.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Recommendation:
    action: str                       # "investigate" | "none"
    dataset_drift_share: float
    persisted_cycles: int
    drifted_features: list[str] = field(default_factory=list)
    reason: str = ""


def promote(detections: list[dict], *, share_threshold: float = 0.3,
            persistence: int = 2) -> Recommendation:
    """detections: chronological list of detector.detect() outputs (oldest->newest).

    action = "investigate" iff dataset_drift_share has stayed above share_threshold for at
    least `persistence` consecutive most-recent cycles (drift-driven + persistence to avoid
    one-off spikes). Returns a recommendation for a HUMAN — no retrain/deploy is triggered.
    """
    if not detections:
        return Recommendation(action="none", dataset_drift_share=0.0, persisted_cycles=0,
                              reason="no drift analysis windows yet")

    # trailing consecutive cycles above threshold (persistence)
    persisted = 0
    for d in reversed(detections):
        if d.get("dataset_drift_share", 0.0) > share_threshold:
            persisted += 1
        else:
            break

    latest = detections[-1]
    drifted = [f["feature"] for f in latest.get("features", []) if f.get("drift")]
    share = float(latest.get("dataset_drift_share", 0.0))

    if persisted >= persistence:
        return Recommendation(
            action="investigate", dataset_drift_share=share, persisted_cycles=persisted,
            drifted_features=drifted,
            reason=(f"dataset drift share > {share_threshold} for {persisted} consecutive "
                    f"cycles (>= {persistence}); drifted features: {drifted}. "
                    f"INVESTIGATE (human): real shift vs sensor/pipeline issue? "
                    f"Drift is a risk signal, not a confirmed performance drop — "
                    f"performance (if available) is auxiliary."))
    return Recommendation(
        action="none", dataset_drift_share=share, persisted_cycles=persisted,
        drifted_features=drifted,
        reason=(f"drift not persistent enough ({persisted} < {persistence} cycles "
                f"above {share_threshold}); watch only, no action."))
