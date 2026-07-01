"""XGB 최소 서빙 앱 (serving-benchmark 1차 핸드오프 §B).

GRU 서빙(`app.py`)과 **같은 `/predict` 계약**을 따르되, 예측/추론 로직을 오염시키지
않는 **독립 FastAPI 앱**이다(B6 — 새 앱). 핵심:
  - XGB는 stateless가 아니다 → **환자별 최근 8행 raw 버퍼**를 유지해 매 요청마다
    `features.lookback_summary`로 (F*7)차원 입력을 서버측 재구성한다(B2).
  - 챔피언 재현을 위해 `.ubj` 임베드 `best_iteration`으로 **트리를 절단**한다
    (`iteration_range=(0, best_iter+1)`, B3). 무효 best_iter는 **명시적 실패**(A3-b).
  - latency 히스토그램(`serve_predict_latency_seconds`)이 **버퍼 재구성 + 절단 추론**을
    함께 감싼다(B5 — GRU predict의 전처리 포함과 대칭). 공유 `metrics.record` 재사용.

`replicas=1` 가정(인메모리 버퍼). 동일 환자 동시 요청은 **환자별 lock**으로 직렬화(M1).
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from sepsis import config as C
from sepsis.data import features as F
from sepsis.drift.window import get_window
from sepsis.serve import metrics
from sepsis.train import tree

# featureset -> 아티팩트 디렉토리(내부에 model/xgboost_<fs>.ubj + preprocess.json).
# run_id 하드코딩 금지(B1): 호출부는 이 매핑/ env 로만 경로를 얻는다. 재학습·다른 mlruns
# 위치에서도 env 로 덮어쓸 수 있다.
_DEFAULT_MODEL_DIRS = {
    "vitals": "mlruns/1/3e21f380b380422d8d52f78904e54ad4/artifacts",
    "vitals_labs": "mlruns/1/fe64aac54f344999baa217f56e4e963c/artifacts",
}


def _model_dir(featureset: str) -> Path:
    """아티팩트 디렉토리. env 우선(featureset별 or 공통), 없으면 기본 매핑."""
    env = os.environ.get(f"SEPSIS_XGB_MODEL_DIR_{featureset.upper()}") or os.environ.get(
        "SEPSIS_XGB_MODEL_DIR"
    )
    if env:
        return Path(env)
    return C.ROOT / _DEFAULT_MODEL_DIRS[featureset]


def _resolve_best_iter(booster) -> int:
    """best_iter = `.ubj` 임베드 `best_iteration`. 단 env `SEPSIS_XGB_BEST_ITER_OVERRIDE`가
    설정되면 그 값을 쓴다. **무효값(0·음수·비정수·'none')이면 조용한 전체-트리 폴백을
    금지하고 명시적으로 실패**한다(A3-b / B3.2). 유효 = 정수 ≥ 1."""
    raw = os.environ.get("SEPSIS_XGB_BEST_ITER_OVERRIDE")
    if raw is not None and raw.strip() != "":
        try:
            bi = int(raw.strip())
        except ValueError as e:
            raise RuntimeError(
                f"invalid SEPSIS_XGB_BEST_ITER_OVERRIDE={raw!r} (not an int) — "
                f"refusing silent full-tree fallback (A3-b)"
            ) from e
    else:
        bi = int(getattr(booster, "best_iteration", -1) or -1)
    if not (isinstance(bi, int) and bi >= 1):
        raise RuntimeError(
            f"best_iter must be a positive int (got {bi!r}) — refusing silent "
            f"full-tree fallback (A3-b). Champion reproduction needs the embedded "
            f"best_iteration or a valid override."
        )
    return bi


class PredictRequest(BaseModel):
    patient_id: str
    features: dict[str, float | None]   # absent/null feature -> NaN (no 0-fill)


def build_app(featureset: str = "vitals", *, metrics_set=None) -> FastAPI:
    """featureset(=vitals|vitals_labs)용 XGB 최소 서빙 앱을 새로 만든다.

    **매 호출이 빈 버퍼의 독립 인스턴스**를 준다(테스트 간 환자 상태 격리). 무효
    best_iter override면 여기서(기동 시) `RuntimeError`를 던진다 — A3-b의 "기동 실패".

    `metrics_set`(2A): 인스턴스별 `MetricSet`(fresh 레지스트리)을 주면 그것으로 관측·
    `/metrics` 렌더 → 부가계측 시계열이 다른 인스턴스와 격리된다. 안 주면 전역 기본.
    관측성 게이트(`SEPSIS_SERVE_AUX_METRICS`)는 **기동 시점에 캡처**해 요청마다 적용한다.
    """
    if featureset not in _DEFAULT_MODEL_DIRS:
        raise ValueError(f"unknown featureset {featureset!r}")
    cols = C.featureset_columns(featureset)
    mdir = _model_dir(featureset)
    booster = tree.load_booster("xgboost", str(mdir / "model" / f"xgboost_{featureset}.ubj"))
    tau = float(json.loads((mdir / "preprocess.json").read_text())["tau"])
    best_iter = _resolve_best_iter(booster)  # may raise -> 기동 실패 (A3-b)
    ms = metrics_set if metrics_set is not None else metrics._DEFAULT
    aux_on = metrics._aux_metrics_enabled()  # 기동 시점 캡처(2A 게이트)

    # per-instance 상태 (module-global 아님 → 인스턴스마다 빈 버퍼)
    buffers: dict[str, deque] = {}
    locks: dict[str, threading.Lock] = {}
    reg_lock = threading.Lock()

    def _lock(pid: str) -> threading.Lock:
        with reg_lock:
            lk = locks.get(pid)
            if lk is None:
                lk = threading.Lock()
                locks[pid] = lk
            return lk

    def _row_from(feat: dict[str, float | None]) -> np.ndarray:
        unknown = set(feat) - set(cols)
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"unknown features {sorted(unknown)}; expected subset of {cols}",
            )
        # absent OR null -> np.nan (결측 계약; 0/mean fill 금지)
        return np.array(
            [feat.get(c) if feat.get(c) is not None else np.nan for c in cols],
            dtype=np.float32,
        )

    app = FastAPI(title="sepsis-xgb-serving", version="bench-1")

    @app.post("/predict")
    def predict(req: PredictRequest) -> dict:
        row = _row_from(req.features)
        # 환자별 lock: 동일 환자 동시 요청의 버퍼 read-modify-write 직렬화(M1).
        with _lock(req.patient_id):
            buf = buffers.get(req.patient_id)
            if buf is None:
                buf = deque(maxlen=C.LOOKBACK)
                buffers[req.patient_id] = buf
            buf.append(row)
            # latency 경계(B5): 버퍼 재구성(lookback_summary) + 절단 추론을 함께 감쌈.
            t0 = time.perf_counter()
            raw_win = np.array(buf, dtype=np.float32)          # (N, F)  N<=8
            summ = F.lookback_summary(raw_win)                 # (N, F*7) NaN-aware 앞패딩
            last = summ[-1:]                                    # (1, F*7) 이번 요청 입력
            p = float(tree.booster_predict(booster, "xgboost", last, best_iter)[0])
            latency = time.perf_counter() - t0
        alarm = bool(p >= tau)
        # 공유 MetricSet.record → serve_predict_latency_seconds 관측(A4) + per-feature 입력분포
        # (2A 부가계측 표면, aux 게이트 대상). observe(latency)는 위 경계값을 그대로 관측.
        ms.record(latency, p, alarm, row, cols, patient_id=req.patient_id, aux=aux_on)
        # ★ 2A 부가계측 표면 — drift 윈도우 적재(GRU와 동형, aux 게이트 대상).
        if aux_on:
            get_window().add(req.patient_id, row)
        return {"patient_id": req.patient_id, "p": p, "alarm": alarm, "featureset": featureset}

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "featureset": featureset, "best_iter": best_iter}

    @app.get("/metrics")
    def metrics_endpoint():
        body, content_type = ms.render()
        return Response(content=body, media_type=content_type)

    return app
