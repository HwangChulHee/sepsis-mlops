"""H4s-b — FastAPI prediction service (결정 2·5).

POST /predict (patient_id + current-timestep features -> {p, alarm}), GET /health,
GET /schema, GET /metrics. ★ Missing contract: features are Optional[float]; an absent
or null feature -> np.nan (NEVER 0 / mean — that is train-serving skew). The accepted
feature set is DERIVED from the loaded run's featureset (unknown keys rejected). Per-patient
state via the stateful predictor (per-pid lock). Bundle is loaded atomically (single run).
"""

from __future__ import annotations

import os
import time
from typing import Optional

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from sepsis import config as C
from sepsis.drift.window import get_window
from sepsis.serve import metrics
from sepsis.serve.bundle import load_bundle
from sepsis.serve.predictor import StatefulPredictor

app = FastAPI(title="sepsis-serving", version="h4s")
_S: dict = {}


def state() -> dict:
    if "pred" not in _S:
        # container: SERVE_BUNDLE_DIR (= /app/deploy/artifacts/$RUN, set via ConfigMap) ->
        # exported dir, no MLflow. dev: fall back to MLflow by featureset.
        bundle_dir = os.environ.get("SERVE_BUNDLE_DIR")
        if bundle_dir:
            b = load_bundle(artifacts_dir=bundle_dir)
        else:
            b = load_bundle(os.environ.get("SERVE_FEATURESET", "vitals"))
        _S.update(bundle=b, pred=StatefulPredictor(b), cols=C.featureset_columns(b.featureset))
    return _S


class PredictRequest(BaseModel):
    patient_id: str
    features: dict[str, Optional[float]]   # absent/null feature -> NaN (no 0-fill)


def _row_from(features: dict[str, Optional[float]], cols: list[str]) -> np.ndarray:
    unknown = set(features) - set(cols)
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown features {sorted(unknown)}; "
                                                    f"expected subset of {cols}")
    # absent OR null -> np.nan (the missing contract; never 0 / mean)
    return np.array([features.get(c) if features.get(c) is not None else np.nan
                     for c in cols], dtype=np.float32)


@app.post("/predict")
def predict(req: PredictRequest) -> dict:
    s = state()
    row = _row_from(req.features, s["cols"])
    t0 = time.perf_counter()
    out = s["pred"].predict(req.patient_id, row)
    metrics.record(time.perf_counter() - t0, out["p"], out["alarm"], row, s["cols"])
    # H4d-b: collect (patient_id, raw_row) for drift monitoring — separate store from the
    # predictor's per-patient hidden state; light (no evidently). Serving behavior unchanged.
    get_window().add(req.patient_id, row)
    return {"patient_id": req.patient_id, "p": out["p"], "alarm": out["alarm"],
            "featureset": s["bundle"].featureset}


@app.get("/health")
def health() -> dict:
    metrics.HEALTH_REQUESTS.inc()
    s = state()
    return {"status": "ok", "run_id": s["bundle"].run_id,
            "featureset": s["bundle"].featureset, "input_dim": s["bundle"].input_dim}


@app.get("/schema")
def schema() -> dict:
    s = state()
    return {"featureset": s["bundle"].featureset, "features": s["cols"],
            "n_features": len(s["cols"])}


@app.get("/metrics")
def metrics_endpoint() -> Response:
    body, content_type = metrics.render()
    return Response(content=body, media_type=content_type)
