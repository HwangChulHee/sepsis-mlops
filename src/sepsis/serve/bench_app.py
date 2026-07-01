"""벤치용 GRU 서빙 앱 팩토리 (serving-benchmark 2A/2B).

프로덕션 `app.py`는 모듈-전역 상태(_S/_DS, singleton app)라 한 프로세스에서 여러 격리
인스턴스를 못 띄운다. 벤치·게이트 테스트는 **인스턴스별로 fresh 예측 상태 + fresh
Prometheus 레지스트리**가 필요하므로(부가계측 시계열·환자 hidden state 격리), 실제 GRU
추론 스택(`load_bundle_from_dir` + `StatefulPredictor` + `StreamPreprocessor`)을 그대로
재사용하되 FastAPI 배선만 새로 만드는 팩토리를 둔다 — 예측/전처리 로직은 프로덕션과 동일.

관측성 게이트(`SEPSIS_SERVE_AUX_METRICS`)·drift 윈도우 적재는 XGB 앱과 동형(2A 대칭).
`replicas=1` 가정(인메모리 상태).
"""

from __future__ import annotations

import functools
import os
import time

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from sepsis import config as C
from sepsis.drift.window import get_window
from sepsis.serve import metrics
from sepsis.serve.bundle import Bundle, load_bundle_from_dir
from sepsis.serve.predictor import StatefulPredictor


@functools.lru_cache(maxsize=4)
def _load_bundle_cached(artifacts_dir: str) -> Bundle:
    """동일 dir 번들은 캐시(불변 frozen Bundle이라 인스턴스 간 공유 안전). 각 앱은 fresh
    StatefulPredictor를 따로 받으므로 per-patient 상태는 여전히 인스턴스별 격리."""
    return load_bundle_from_dir(artifacts_dir)


class PredictRequest(BaseModel):
    patient_id: str
    features: dict[str, float | None]   # absent/null feature -> NaN (no 0-fill)


def build_gru_app(*, artifacts_dir=None, metrics_set=None) -> FastAPI:
    """GRU 서빙 앱을 새로 만든다(fresh 예측 상태). 프로덕션과 같은 예측 스택 재사용.

    artifacts_dir: 내보낸 GRU 번들 dir(meta.json+pre.npz+model.pt). 기본 = deploy 활성 별칭.
    metrics_set: 인스턴스별 `MetricSet`(fresh 레지스트리)이면 부가계측 격리. 안 주면 전역 기본.
    """
    adir = str(artifacts_dir) if artifacts_dir is not None else os.environ.get(
        "SEPSIS_GRU_ARTIFACTS_DIR", str(C.ROOT / "deploy" / "artifacts" / "gru_vitals")
    )
    bundle = _load_bundle_cached(adir)
    pred = StatefulPredictor(bundle)             # ★ 인스턴스별 fresh per-patient 상태
    cols = C.featureset_columns(bundle.featureset)
    ms = metrics_set if metrics_set is not None else metrics._DEFAULT
    aux_on = metrics._aux_metrics_enabled()      # 기동 시점 캡처(2A 게이트)

    def _row_from(feat: dict[str, float | None]) -> np.ndarray:
        unknown = set(feat) - set(cols)
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"unknown features {sorted(unknown)}; expected subset of {cols}",
            )
        return np.array(
            [feat.get(c) if feat.get(c) is not None else np.nan for c in cols],
            dtype=np.float32,
        )

    app = FastAPI(title="sepsis-gru-serving(bench)", version="bench-1")

    @app.post("/predict")
    def predict(req: PredictRequest) -> dict:
        row = _row_from(req.features)
        # latency 경계(B5): GRU predict()가 전처리(StreamPreprocessor)를 포함하므로 그대로 감쌈.
        t0 = time.perf_counter()
        out = pred.predict(req.patient_id, row)
        latency = time.perf_counter() - t0
        ms.record(latency, out["p"], out["alarm"], row, cols,
                  patient_id=req.patient_id, aux=aux_on)
        if aux_on:   # ★ 부가계측: drift 윈도우 적재(2A 게이트 대상)
            get_window().add(req.patient_id, row)
        return {"patient_id": req.patient_id, "p": out["p"], "alarm": out["alarm"],
                "featureset": bundle.featureset}

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "featureset": bundle.featureset, "run_id": bundle.run_id}

    @app.get("/metrics")
    def metrics_endpoint():
        body, content_type = ms.render()
        return Response(content=body, media_type=content_type)

    return app
