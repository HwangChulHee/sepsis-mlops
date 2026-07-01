"""구현 1 — 감사 저장소 (SQLAlchemy ORM). 결정 4.

단일 테이블 `audit_events`, append-only(정정·삭제 불가 — 정정도 새 레코드로만). SQLite 시작,
engine URL 주입으로 PostgreSQL 교체 가능. append-only는 **두 경로 모두** 막는다(MJ2):
ORM unit-of-work(before_flush)와 bulk(do_orm_execute) — before_flush는 bulk를 못 본다.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, create_engine, event
from sqlalchemy.orm import Session, declarative_base, sessionmaker

Base = declarative_base()


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id = Column(Integer, primary_key=True)                       # 자동 증가
    ts = Column(DateTime, nullable=False)                        # UTC, 전용칸(검색)
    event_type = Column(String, nullable=False)                  # APPROVE|ROLLBACK|RECONCILE|BOOTSTRAP
    featureset = Column(String, nullable=False)                  # 직렬화 키 단위
    gate_passed = Column(Boolean, nullable=True)                 # no_regression. ROLLBACK/RECONCILE/BOOTSTRAP은 NULL
    from_version = Column(String, nullable=True)                 # prev(이전 활성), 콜드스타트는 NULL. 디렉토리명(B1)
    to_version = Column(String, nullable=False)                  # 대상(새 활성). 디렉토리명(B1)
    run_id = Column(String, nullable=True)                       # MLflow 링크 키(박제). meta.json 출처
    git_commit = Column(String, nullable=True)                   # retrain.json 출처(MJ1)
    gate_snapshot = Column(JSON, nullable=True)                  # validation.json 통째 사본(박제)
    actor_unverified = Column(String, nullable=False, default="operator")  # M4: 미검증 입력 명시
    verified_subject = Column(String, nullable=True)             # M4: SSO/OIDC 예약, MVP는 NULL
    reason = Column(String, nullable=False, default="")          # 자유 텍스트


# === append-only 강제 — 전역 Session에 등록(어떤 세션이든 지배) ===
@event.listens_for(Session, "before_flush")
def _block_uow_mutation(session, ctx, _):
    """(1) ORM unit-of-work: 로드한 인스턴스 UPDATE/DELETE 차단. INSERT(new)는 통과."""
    if session.dirty or session.deleted:
        raise PermissionError("audit_events is append-only (no instance UPDATE/DELETE)")


@event.listens_for(Session, "do_orm_execute")
def _block_bulk_mutation(state):
    """(2) bulk 경로: Query/Core UPDATE·DELETE 차단(before_flush 우회 봉쇄, MJ2). SELECT는 통과."""
    if state.is_update or state.is_delete:
        raise PermissionError("audit_events is append-only (no bulk UPDATE/DELETE)")


class AuditStore:
    """서비스 계층이 부르는 얇은 API. INSERT(append)·SELECT(query)만, UPDATE/DELETE 없음."""

    def __init__(self, url: str = "sqlite:///console_audit.db"):
        self.engine = create_engine(url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def append(self, **fields) -> AuditEvent:
        """단일 INSERT. ts 미지정 시 tz-aware UTC now(utcnow deprecation 회피)."""
        fields.setdefault("ts", dt.datetime.now(dt.UTC))
        ev = AuditEvent(**fields)
        s = self.Session()
        try:
            s.add(ev)
            s.commit()
            s.refresh(ev)        # 커밋 후 속성 로드(SELECT — do_orm_execute is_select 허용)
            s.expunge(ev)        # 세션 닫혀도 속성 접근 가능하게 detach
            return ev
        finally:
            s.close()

    def last_active(self, featureset: str) -> AuditEvent | None:
        """최근 APPROVE/ROLLBACK/RECONCILE/BOOTSTRAP 1건(감사상 최종 활성)."""
        s = self.Session()
        try:
            ev = (s.query(AuditEvent)
                  .filter(AuditEvent.featureset == featureset)
                  .order_by(AuditEvent.id.desc())
                  .first())
            if ev is not None:
                s.expunge(ev)
            return ev
        finally:
            s.close()

    def query(self, *, event_type=None, gate_passed=None, since=None, until=None,
              featureset=None) -> list[AuditEvent]:
        """전용칸 필터(SELECT)."""
        s = self.Session()
        try:
            q = s.query(AuditEvent)
            if event_type is not None:
                q = q.filter(AuditEvent.event_type == event_type)
            if gate_passed is not None:
                q = q.filter(AuditEvent.gate_passed == gate_passed)
            if featureset is not None:
                q = q.filter(AuditEvent.featureset == featureset)
            if since is not None:
                q = q.filter(AuditEvent.ts >= since)
            if until is not None:
                q = q.filter(AuditEvent.ts <= until)
            rows = q.order_by(AuditEvent.id).all()
            for r in rows:
                s.expunge(r)
            return rows
        finally:
            s.close()
