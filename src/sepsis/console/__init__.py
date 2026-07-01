"""H4 운영 콘솔 백엔드 (핸드오프 A) — 감사 ORM + /console API + 트랜잭션 경계.

설계: docs/design/console/decisions.md (결정 4·5·5-A·5-B·6-A·7).
명세: docs/design/console/handoff_backend.md.
선행(console-prep): validation.json/retrain.json/.ready 영속, meta.json.run_id,
서빙 /admin/reload·/health.run_id 위에 얹는다.
"""
