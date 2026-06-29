"""구현 2 — /console API (FastAPI, 5 엔드포인트). 결정 5.

얇은 어댑터: service 함수를 호출만. ValueError/FileNotFoundError → 422(미완성·교차-fs·REGRESSED).
PermissionError → 403. 로직·직렬화·감사·복원은 전부 service 계층.
"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from sepsis.console import service

app = FastAPI(title="sepsis-console", version="A", lifespan=service.lifespan)


class WriteRequest(BaseModel):
    fs: str
    version: str                     # 버전 디렉토리명(gru_<fs>@<v>, B1)
    actor: str
    reason: str = ""


def _serialize_event(ev) -> dict:
    return {"id": ev.id, "ts": ev.ts.isoformat() if ev.ts else None,
            "event_type": ev.event_type, "featureset": ev.featureset,
            "gate_passed": ev.gate_passed, "from_version": ev.from_version,
            "to_version": ev.to_version, "run_id": ev.run_id, "git_commit": ev.git_commit,
            "actor_unverified": ev.actor_unverified, "verified_subject": ev.verified_subject,
            "reason": ev.reason}


# === 읽기 3 ===
@app.get("/console/versions")
def versions(fs: str = Query(...)) -> dict:
    return service.list_versions(fs)


@app.get("/console/versions/{version}")
def version_detail(version: str, fs: str = Query(...)) -> dict:
    return service.get_version_detail(fs, version)


@app.get("/console/audit")
def audit_query(event_type: Optional[str] = None, gate_passed: Optional[bool] = None,
                since: Optional[str] = None, fs: Optional[str] = None) -> list[dict]:
    rows = service.audit.query(event_type=event_type, gate_passed=gate_passed,
                               since=since, featureset=fs)
    return [_serialize_event(r) for r in rows]


# === 쓰기 2 (직렬화 경계·감사는 service가 강제) ===
@app.post("/console/approve")
def approve(req: WriteRequest) -> dict:
    try:
        return service.approve(req.fs, req.version, actor=req.actor, reason=req.reason)
    except (ValueError, FileNotFoundError) as e:      # 교차-fs·미완성·REGRESSED
        raise HTTPException(status_code=422, detail=str(e))
    except PermissionError as e:                       # 미승인
        raise HTTPException(status_code=403, detail=str(e))


@app.post("/console/rollback")
def rollback(req: WriteRequest) -> dict:
    try:
        return service.rollback(req.fs, req.version, actor=req.actor, reason=req.reason)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
