"""성공기준 1 — 감사 append-only 불변성(두 경로) + ts UTC.

검증 대상(handoff:76-98, 248, 결정 4 / MJ2):
- (a) ORM unit-of-work 경로: 로드한 인스턴스 UPDATE/DELETE 후 flush 는 PermissionError
      (before_flush 훅, session.dirty/deleted).
- (b) bulk 우회 경로: session.execute(update(AuditEvent)...) / delete(...) 도 PermissionError
      (do_orm_execute 훅, state.is_update/is_delete) — before_flush 가 못 보는 구멍을 봉쇄.
- INSERT(append)는 통과. SELECT(query)도 통과. ts 미지정 시 UTC utcnow.

src/ 구현 코드는 읽지 않았다 — 핸드오프가 명세한 ORM 심볼/훅 계약만 신뢰한다.
"""
from __future__ import annotations

import datetime as dt

import pytest


def _fresh_session(db_path):
    """audit import(전역 Session 리스너 등록) 후, 같은 DB 에 세션 1개.

    리스너는 sqlalchemy.orm.Session 전역에 걸리므로(handoff:85·91) 어떤 세션이든 지배된다.
    [검증 필요] 선행: audit.Base(declarative base)·audit.AuditEvent 가 export 됨(handoff:55).
    """
    import sepsis.console.audit as audit_mod  # import 시 두 훅 등록
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{db_path}")
    audit_mod.Base.metadata.create_all(engine)  # DDL(엔진 경로) — 훅 무관
    Session = sessionmaker(bind=engine)
    return audit_mod, Session()


def _insert_one(audit_mod, session):
    ev = audit_mod.AuditEvent(
        ts=dt.datetime.utcnow(), event_type="APPROVE", featureset="vitals",
        from_version="gru_vitals@v1", to_version="gru_vitals@v2",
    )
    session.add(ev)
    session.commit()  # 성공기준 1 — INSERT(append)는 통과
    return ev


# ===== (a) ORM unit-of-work UPDATE 차단 =====
def test_orm_instance_update_blocked(tmp_path):
    # 성공기준 1 (a) before_flush
    audit_mod, s = _fresh_session(tmp_path / "a.db")
    ev = _insert_one(audit_mod, s)
    ev.reason = "tampered"          # 로드한 인스턴스 변이 → session.dirty
    with pytest.raises(PermissionError):
        s.flush()


# ===== (a) ORM unit-of-work DELETE 차단 =====
def test_orm_instance_delete_blocked(tmp_path):
    # 성공기준 1 (a) before_flush
    audit_mod, s = _fresh_session(tmp_path / "b.db")
    ev = _insert_one(audit_mod, s)
    s.delete(ev)                    # session.deleted
    with pytest.raises(PermissionError):
        s.flush()


# ===== (b) bulk UPDATE 우회 차단 (MJ2) =====
def test_bulk_update_blocked(tmp_path):
    # 성공기준 1 (b) do_orm_execute — before_flush 우회 구멍 봉쇄
    from sqlalchemy import update
    audit_mod, s = _fresh_session(tmp_path / "c.db")
    _insert_one(audit_mod, s)
    with pytest.raises(PermissionError):
        s.execute(update(audit_mod.AuditEvent).values(reason="bulk-tamper"))


# ===== (b) bulk DELETE 우회 차단 (MJ2) =====
def test_bulk_delete_blocked(tmp_path):
    # 성공기준 1 (b) do_orm_execute
    from sqlalchemy import delete
    audit_mod, s = _fresh_session(tmp_path / "d.db")
    _insert_one(audit_mod, s)
    with pytest.raises(PermissionError):
        s.execute(delete(audit_mod.AuditEvent))


# ===== INSERT(append)는 통과 + SELECT 통과 =====
def test_append_insert_and_select_pass(tmp_path):
    # 성공기준 1 — append(INSERT)·query(SELECT)는 막히지 않는다
    from sepsis.console.audit import AuditStore
    store = AuditStore(url=f"sqlite:///{tmp_path / 'e.db'}")
    ev = store.append(event_type="APPROVE", featureset="vitals",
                      to_version="gru_vitals@v1")
    assert ev.id is not None, "append INSERT 가 막혔다(append-only 가 INSERT 까지 차단하면 오류)"
    rows = store.query(featureset="vitals")  # SELECT 는 통과해야 함(do_orm_execute is_select)
    assert any(r.to_version == "gru_vitals@v1" for r in rows)


# ===== ts 미지정 시 UTC utcnow =====
def test_append_ts_defaults_to_utc_now(tmp_path):
    # 성공기준 1 — ts 미지정시 utcnow(handoff:105)
    from sepsis.console.audit import AuditStore
    store = AuditStore(url=f"sqlite:///{tmp_path / 'f.db'}")
    before = dt.datetime.utcnow()
    ev = store.append(event_type="APPROVE", featureset="vitals",
                      to_version="gru_vitals@v1")
    after = dt.datetime.utcnow()
    assert ev.ts is not None
    # naive UTC 로 채워짐(현재 시각 근방) — 로컬타임/None 이 아님
    assert before - dt.timedelta(seconds=5) <= ev.ts <= after + dt.timedelta(seconds=5)
