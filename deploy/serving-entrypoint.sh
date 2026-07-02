#!/bin/sh
# serving 컨테이너 entrypoint — seed precondition 확인 후 uvicorn 기동.
# (핸드오프 onprem-compose §0·§2.1: app.py lifespan 훅(M2-2) 대신 배관 셸로 전제조건 확인 →
#  serving 파이썬 코드 0줄 변경. 번들 유무는 파일시스템 사실이라 배관의 관심사다.)
ARTIFACTS_DIR=${ARTIFACTS_DIR:-/app/deploy/artifacts}
FS=${SERVE_FEATURESET:-vitals}
if [ ! -e "$ARTIFACTS_DIR/gru_$FS" ]; then
  echo "FATAL: active alias 'gru_$FS' missing under $ARTIFACTS_DIR." >&2
  echo "  seed first (host): uv run python -m scripts.h4.h4s_export_bundle vitals" >&2
  echo "  then (restart:\"no\" 이므로 수동 재기동): docker compose up -d serving" >&2
  exit 3   # 종료코드 3 = seed precondition 실패. restart:"no"와만 결합해야 crash-loop 없음(§2.1 B1).
fi
# 기존 CMD 인자 그대로 보존(--log-level ${LOG_LEVEL:-info}). exec 로 uvicorn 을 PID1 승격 → SIGTERM graceful.
exec uvicorn sepsis.serve.app:app --host 0.0.0.0 --port 8000 --log-level ${LOG_LEVEL:-info}
