# H4s-c — sepsis serving image (self-contained, no external repo).
# Lean serving runtime: torch(CPU) + fastapi + numpy only; the model bundle is the
# pre-exported deploy/artifacts/ dir (no MLflow/pandas/xgboost in the image).
FROM python:3.12-slim

WORKDIR /app

# torch CPU wheel from the official CPU index, then the lightweight serving deps.
# ★ 버전은 uv.lock 과 정확히 일치시킨다 — 안 그러면 컨테이너가 dev/test 와 다른 torch/numpy 를
# 받아(빌드 시점 최신) 모델 로드·수치가 갈라진다(train/serve 스큐, 재현 불가 빌드). lock 갱신 시
# 여기도 함께 올릴 것.
RUN pip install --no-cache-dir torch==2.12.1 --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir \
      "fastapi==0.138.1" "uvicorn[standard]==0.49.0" "prometheus-client==0.25.0" \
      "numpy==2.5.0" "pandas==2.3.3" "scipy==1.18.0" "pydantic==2.13.4"
# pandas·scipy: app.py → drift.{reference,run,synthetic} import 체인에 필요
#   (data.cache→pandas, drift.distance→scipy.spatial/stats). 빠지면 컨테이너에서 app import
#   자체가 ModuleNotFoundError로 실패(잠복 버그, E2E에서 발견). mlflow는 bundle.py에서 함수내
#   lazy import라 dir 기반 로드(load_bundle_from_dir) 경로엔 불필요 — 경량 유지.

# app code only. The model bundle lives on the shared PVC mounted at /app/deploy/artifacts
# at runtime (B2) — baking deploy/artifacts/ into the image would just be shadowed by that
# mount (dead layer + a trap: stale baked alias if the mount ever misses). Pre-create the dir
# so serve's alias resolution is graceful (empty) before the PVC mounts, mirroring Dockerfile.api.
COPY src/ /app/src/
RUN mkdir -p /app/deploy/artifacts

# src layout: config.ROOT = parents[2] of /app/src/sepsis/config.py = /app
# Serving resolves the active alias gru_<fs> under ARTIFACTS (default /app/deploy/artifacts).
# The legacy SERVE_BUNDLE_DIR fixed-path env is superseded by alias unification (mn-B) — removed.
ENV PYTHONPATH=/app/src

EXPOSE 8000
# sh -c + exec: LOG_LEVEL env(configmap 주입)을 uvicorn --log-level 로 실제 소비한다.
# exec 로 uvicorn 을 PID 1 로 승격 → K8s SIGTERM graceful drain 유지(sh 가 신호를 안 삼킴).
CMD ["/bin/sh", "-c", "exec uvicorn sepsis.serve.app:app --host 0.0.0.0 --port 8000 --log-level ${LOG_LEVEL:-info}"]
