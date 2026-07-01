"""H4s-b — FastAPI prediction service (결정 2·5).

POST /predict (patient_id + current-timestep features -> {p, alarm}), GET /health,
GET /schema, GET /metrics. ★ Missing contract: features are Optional[float]; an absent
or null feature -> np.nan (NEVER 0 / mean — that is train-serving skew). The accepted
feature set is DERIVED from the loaded run's featureset (unknown keys rejected). Per-patient
state via the stateful predictor (per-pid lock). Bundle is loaded atomically (single run).
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from sepsis import config as C
from sepsis.drift import reference as R
from sepsis.drift import run as drift_run
from sepsis.drift import synthetic
from sepsis.drift.window import get_window
from sepsis.serve import metrics
from sepsis.serve.bundle import load_bundle_from_dir
from sepsis.serve.predictor import StatefulPredictor

app = FastAPI(title="sepsis-serving", version="h4s")
_S: dict = {}
_DS: dict = {}

# Bundle source = the active alias gru_<fs> under ARTIFACTS (결정 5). ARTIFACTS defaults to an
# ABSOLUTE path matching deploy.py:25 (MJ-e) — a relative "deploy/artifacts" is cwd-dependent and
# would resolve to a different alias than the one deploy wrote. The legacy SERVE_BUNDLE_DIR fixed
# path / dev MLflow fallback are SUPERSEDED by this alias unification (state() and drift_state()
# both read the same alias — single source for dev and container).
ARTIFACTS = Path(os.environ.get("ARTIFACTS_DIR", str(C.ROOT / "deploy" / "artifacts")))

_LOCK = threading.Lock()


def _resolve_alias(fs: str) -> Path:
    return (ARTIFACTS / f"gru_{fs}").resolve()   # alias symlink 1회 해석 → 고정 version dir


def _load_all(version_dir: Path, *, force: bool = True) -> None:
    """Load _S(bundle/pred/cols) AND _DS(ref/thr/min_patients) from the SAME version_dir, then
    rebind both module globals atomically (결정 6, B1, MJ-d). force=False is the lazy-boot path:
    if another thread already loaded under the lock, skip (avoids a duplicate 300-trial
    calibration when /predict and /drift race on boot — mn-A). /admin/reload uses force=True."""
    global _S, _DS
    with _LOCK:
        if not force and "pred" in _S and "ref" in _DS:
            return                                       # mn-A: already loaded by another thread
        b = load_bundle_from_dir(version_dir)
        ref = R.load_reference(version_dir / "reference.npz")   # ★ .npz FILE path, not a directory
        wn = int(os.environ.get("DRIFT_WINDOW_N", "500"))
        nt = int(os.environ.get("DRIFT_CAL_TRIALS", "300"))
        new_s = {"bundle": b, "pred": StatefulPredictor(b),
                 "cols": C.featureset_columns(b.featureset)}
        new_ds = {"ref": ref,                            # ★ exactly drift_endpoint's schema
                  "thr": synthetic.calibrate(ref, window_n=wn, n_trials=nt),
                  "min_patients": wn}
        _S = new_s          # single-name rebind (atomic under GIL) — readers see old OR new dict
        _DS = new_ds        #   〃 (never a partially-mutated dict)


def state() -> dict:
    if "pred" not in _S:
        _load_all(_resolve_alias(os.environ.get("SERVE_FEATURESET", "vitals")), force=False)
    return _S


class PredictRequest(BaseModel):
    patient_id: str
    features: dict[str, float | None]   # absent/null feature -> NaN (no 0-fill)


def _row_from(features: dict[str, float | None], cols: list[str]) -> np.ndarray:
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
    metrics.record(time.perf_counter() - t0, out["p"], out["alarm"], row, s["cols"],
                   patient_id=req.patient_id)   # 환자별 최신 위험도 Gauge(옵트인) — 라운드 다
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


def drift_state() -> dict:
    """Drift baseline = the ACTIVE alias bundle's reference.npz (so it rolls back WITH the model —
    see retrain.deploy). Routes through the SAME alias→version_dir as state() via _load_all (B1-3):
    if /drift arrives before /predict, _S·_DS still load together from one version_dir, so the
    baseline matches the deployed model (no SERVE_BUNDLE_DIR/build_reference regression)."""
    if "ref" not in _DS:
        _load_all(_resolve_alias(os.environ.get("SERVE_FEATURESET", "vitals")), force=False)
    return _DS


@app.post("/admin/reload")
def admin_reload() -> dict:
    """Dev in-place reload: re-resolve the alias and load the new active version (bundle +
    reference + thr). Production transfers via K8s rolling restart (콘솔이 트리거)."""
    fs = os.environ.get("SERVE_FEATURESET", "vitals")
    _load_all(_resolve_alias(fs))                # force=True — always reload to the current alias
    return {"reloaded": True, "version_dir": _resolve_alias(fs).name}


@app.get("/drift")
def drift_endpoint() -> dict:
    """Run the drift test on the live window vs the active-bundle reference; publish gauges.
    Watch-only — promotion to action lives in retrain.promote (human-in-the-loop)."""
    ds = drift_state()
    det = drift_run.analyze(ds["ref"], get_window(), ds["thr"], min_patients=ds["min_patients"])
    if det is None:
        return {"status": "insufficient", "n_patients": get_window().n_patients(),
                "min_patients": ds["min_patients"]}
    return {"status": "ok", **det}
