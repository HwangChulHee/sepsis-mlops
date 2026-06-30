"""구현 2 — /console API (FastAPI, 5 엔드포인트). 결정 5.

얇은 어댑터: service 함수를 호출만. ValueError/FileNotFoundError → 422(미완성·교차-fs·REGRESSED).
PermissionError → 403. 로직·직렬화·감사·복원은 전부 service 계층.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from sepsis.console import service

app = FastAPI(title="sepsis-console", version="A", lifespan=service.lifespan)


class WriteRequest(BaseModel):
    fs: str
    version: str                     # 버전 디렉토리명(gru_<fs>@<v>, B1)
    actor: str = Field(min_length=1)         # 빈 actor 감사 기록 차단 → 422 (MINOR 결함 4)
    reason: str = Field("", max_length=2000)


def _iso_utc_z(ts) -> Optional[str]:
    # ts는 UTC로 저장(naive 또는 tz-aware). 'Z' 접미 UTC isoformat으로 통일(deploy.py:61 ...Z 규약).
    if ts is None:
        return None
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts.isoformat() + "Z"


def _serialize_event(ev) -> dict:
    return {"id": ev.id, "ts": _iso_utc_z(ev.ts),
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
                since: Optional[str] = None, until: Optional[str] = None,
                fs: Optional[str] = None) -> list[dict]:
    # since/until 은 ISO 문자열 → datetime 파싱 후 service.query 의 DateTime 비교에 전달.
    # 문자열을 그대로 넘기면 audit.py:99 DateTime 비교가 어긋난다(MINOR 결함 5). 잘못된 형식 → 422.
    since_dt = _parse_iso("since", since)
    until_dt = _parse_iso("until", until)
    rows = service.audit.query(event_type=event_type, gate_passed=gate_passed,
                               since=since_dt, until=until_dt, featureset=fs)
    return [_serialize_event(r) for r in rows]


def _parse_iso(name: str, value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"invalid {name} (expected ISO 8601): {value}") from e


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
