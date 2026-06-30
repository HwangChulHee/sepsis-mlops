# H4s-c — sepsis serving image (self-contained, no external repo).
# Lean serving runtime: torch(CPU) + fastapi + numpy only; the model bundle is the
# pre-exported deploy/artifacts/ dir (no MLflow/pandas/xgboost in the image).
FROM python:3.12-slim

WORKDIR /app

# torch CPU wheel from the official CPU index, then the lightweight serving deps.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir \
      "fastapi>=0.110" "uvicorn[standard]>=0.29" "prometheus-client>=0.20" \
      "numpy>=2.0,<3" "pandas>=2.2,<3" "scipy>=1.11" "pydantic>=2"
# pandas·scipy: app.py → drift.{reference,run,synthetic} import 체인에 필요
#   (data.cache→pandas, drift.distance→scipy.spatial/stats). 빠지면 컨테이너에서 app import
#   자체가 ModuleNotFoundError로 실패(잠복 버그, E2E에서 발견). mlflow는 bundle.py에서 함수내
#   lazy import라 dir 기반 로드(load_bundle_from_dir) 경로엔 불필요 — 경량 유지.

# app code + the portable model bundle (exported via scripts/h4s_export_bundle.py)
COPY src/ /app/src/
COPY deploy/artifacts/ /app/deploy/artifacts/

# src layout: config.ROOT = parents[2] of /app/src/sepsis/config.py = /app
# Serving resolves the active alias gru_<fs> under ARTIFACTS (default /app/deploy/artifacts).
# The legacy SERVE_BUNDLE_DIR fixed-path env is superseded by alias unification (mn-B) — removed.
ENV PYTHONPATH=/app/src

EXPOSE 8000
CMD ["uvicorn", "sepsis.serve.app:app", "--host", "0.0.0.0", "--port", "8000"]
