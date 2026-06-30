"""API 어댑터 입력 검증 — MINOR 결함 4·5 회귀 방지.

검증 대상(src/sepsis/console/api.py):
- WriteRequest.actor 빈값 → 422 (빈 actor 감사 기록 차단, 결함 4). service 도달 전 pydantic 차단.
- /console/audit since/until = ISO 문자열 → datetime 파싱 후 필터(결함 5).
  · since 필터가 실제로 동작(이전 이벤트 제외).
  · until 쿼리 파라미터가 노출되고 동작.
  · 잘못된 ISO 형식 → 422.
"""
from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from sepsis.console import api as api_mod
from sepsis.console.audit import AuditStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    # api 가 쓰는 service.audit 전역을 격리 store 로 교체(기본 상대경로 파일 회피).
    store = AuditStore(url=f"sqlite:///{tmp_path / 'api_audit.db'}")
    monkeypatch.setattr(api_mod.service, "audit", store, raising=False)
    return TestClient(api_mod.app), store


# ===== 결함 4: 빈 actor → 422 (감사 도달 전 차단) =====
def test_approve_empty_actor_rejected_422(client):
    c, store = client
    r = c.post("/console/approve", json={"fs": "vitals", "version": "gru_vitals@v1",
                                         "actor": "", "reason": "x"})
    assert r.status_code == 422, "빈 actor 가 거부되지 않음(결함 4)"
    # 감사 미기록(요청이 service 에 도달하지 않음)
    assert store.query(featureset="vitals") == []


def test_rollback_empty_actor_rejected_422(client):
    c, _ = client
    r = c.post("/console/rollback", json={"fs": "vitals", "version": "gru_vitals@v1",
                                          "actor": ""})
    assert r.status_code == 422


# ===== 결함 5: since 필터가 실제로 동작 =====
def test_audit_since_filters_older_events(client):
    c, store = client
    fs = "vitals"
    store.append(event_type="APPROVE", featureset=fs, to_version="gru_vitals@old",
                 ts=dt.datetime(2026, 1, 1, 0, 0, 0))
    store.append(event_type="APPROVE", featureset=fs, to_version="gru_vitals@new",
                 ts=dt.datetime(2026, 3, 1, 0, 0, 0))

    r = c.get("/console/audit", params={"fs": fs, "since": "2026-02-01T00:00:00"})
    assert r.status_code == 200
    tos = [e["to_version"] for e in r.json()]
    assert tos == ["gru_vitals@new"], f"since 필터 미동작: {tos}"


# ===== 결함 5: until 쿼리 파라미터 노출·동작 =====
def test_audit_until_exposed_and_filters(client):
    c, store = client
    fs = "vitals"
    store.append(event_type="APPROVE", featureset=fs, to_version="gru_vitals@old",
                 ts=dt.datetime(2026, 1, 1, 0, 0, 0))
    store.append(event_type="APPROVE", featureset=fs, to_version="gru_vitals@new",
                 ts=dt.datetime(2026, 3, 1, 0, 0, 0))

    r = c.get("/console/audit", params={"fs": fs, "until": "2026-02-01T00:00:00"})
    assert r.status_code == 200
    tos = [e["to_version"] for e in r.json()]
    assert tos == ["gru_vitals@old"], f"until 필터 미동작/미노출: {tos}"


# ===== 결함 5: 잘못된 ISO 형식 → 422 =====
def test_audit_invalid_since_format_422(client):
    c, _ = client
    r = c.get("/console/audit", params={"fs": "vitals", "since": "not-a-date"})
    assert r.status_code == 422


# ===== ts 직렬화는 'Z' 접미 UTC isoformat (결함 6 일관성) =====
def test_audit_ts_serialized_with_z_suffix(client):
    c, store = client
    fs = "vitals"
    store.append(event_type="APPROVE", featureset=fs, to_version="gru_vitals@v1",
                 ts=dt.datetime(2026, 1, 1, 12, 0, 0))
    r = c.get("/console/audit", params={"fs": fs})
    assert r.status_code == 200
    ts = r.json()[0]["ts"]
    assert ts.endswith("Z"), f"ts 직렬화가 Z 접미 UTC 아님: {ts}"
