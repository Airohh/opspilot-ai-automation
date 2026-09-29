"""Local request index and append-only audit trail."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class Base(DeclarativeBase):
    pass


class RequestCounter(Base):
    __tablename__ = "request_counters"

    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, default=0)


class OperationRequest(Base):
    __tablename__ = "operation_requests"

    request_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    requester: Mapped[str] = mapped_column(String(200))
    request_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32))
    human_approval: Mapped[str] = mapped_column(String(32))
    notion_page_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    waiting_approval_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ai_analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_impact: Mapped[str | None] = mapped_column(Text, nullable=True)
    tools_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "requester": self.requester,
            "request": self.request_text,
            "status": self.status,
            "human_approval": self.human_approval,
            "notion_page_id": self.notion_page_id,
            "created_at": _iso(self.created_at),
            "waiting_approval_at": _iso(self.waiting_approval_at),
            "processed_at": _iso(self.processed_at),
            "ai_analysis": self.ai_analysis,
            "proposed_action": self.proposed_action,
            "expected_impact": self.expected_impact,
            "tools_used": _load_tools(self.tools_used),
            "approved_by": self.approved_by,
            "error_message": self.error_message,
        }


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(32), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    requester: Mapped[str | None] = mapped_column(String(200), nullable=True)
    request: Mapped[str | None] = mapped_column(Text, nullable=True)
    tools_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    execution_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    execution_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_type: Mapped[str] = mapped_column(String(32))

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "timestamp": _iso(self.timestamp),
            "requester": self.requester,
            "request": self.request,
            "tools_used": _load_tools(self.tools_used),
            "agent_decision": self.agent_decision,
            "proposed_action": self.proposed_action,
            "approval_status": self.approval_status,
            "approved_by": self.approved_by,
            "execution_status": self.execution_status,
            "execution_time_ms": self.execution_time_ms,
            "error_message": self.error_message,
            "event_type": self.event_type,
        }


def make_engine(database_url: str):
    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)


def init_db(engine) -> sessionmaker[Session]:
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def next_request_id(session: Session, now: datetime | None = None) -> str:
    moment = now or utcnow()
    counter = session.get(RequestCounter, moment.year)
    if counter is None:
        counter = RequestCounter(year=moment.year, last_value=1)
        session.add(counter)
    else:
        counter.last_value += 1
    session.flush()
    return f"REQ-{moment.year}-{counter.last_value:03d}"


def add_event(session: Session, request: OperationRequest, event_type: str, **fields) -> AuditEvent:
    event = AuditEvent(
        request_id=request.request_id,
        timestamp=utcnow(),
        requester=request.requester,
        request=request.request_text,
        tools_used=request.tools_used,
        proposed_action=request.proposed_action,
        approval_status=request.human_approval,
        approved_by=request.approved_by,
        error_message=request.error_message,
        execution_status=fields.pop("execution_status", None),
        execution_time_ms=fields.pop("execution_time_ms", None),
        agent_decision=fields.pop("agent_decision", None),
        event_type=event_type,
    )
    if fields:
        unexpected = ", ".join(sorted(fields))
        raise TypeError(f"Unexpected audit fields: {unexpected}")
    session.add(event)
    return event


def execution_time_ms(request: OperationRequest, now: datetime | None = None) -> int:
    start = as_utc(request.created_at)
    end = as_utc(now or utcnow())
    if start is None or end is None:
        return 0
    return max(0, int((end - start).total_seconds() * 1000))


def average_ms(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def build_kpi(
    session: Session,
    llm_cost_per_request: float,
    minutes_saved_per_request: float,
) -> dict:
    rows = list(session.scalars(select(OperationRequest)).all())
    processed = len(rows)
    successful = sum(1 for row in rows if row.status == "Completed")
    failed = sum(1 for row in rows if row.status == "Failed")
    approved = sum(1 for row in rows if row.human_approval == "Approved")
    rejected = sum(1 for row in rows if row.human_approval == "Rejected")
    decided = approved + rejected
    processing_times: list[float] = []
    review_times: list[float] = []
    for row in rows:
        created = as_utc(row.created_at)
        processed_at = as_utc(row.processed_at)
        waiting = as_utc(row.waiting_approval_at)
        if created and processed_at:
            processing_times.append((processed_at - created).total_seconds() * 1000)
        if waiting and processed_at:
            review_times.append((processed_at - waiting).total_seconds() * 1000)
    hours_saved = None
    if successful:
        hours_saved = round(successful * minutes_saved_per_request / 60, 2)
    return {
        "source": "audit_log",
        "requests_processed": processed,
        "successful_executions": successful,
        "failed_executions": failed,
        "approval_rate": None if decided == 0 else round(approved / decided, 4),
        "rejection_rate": None if decided == 0 else round(rejected / decided, 4),
        "automation_rate": None if processed == 0 else round(successful / processed, 4),
        "average_processing_time_ms": average_ms(processing_times),
        "average_human_review_time_ms": average_ms(review_times),
        "estimated_hours_saved": {
            "value": hours_saved,
            "simulation": True,
            "note": (
                "Applies the configured minutes-saved assumption only to requests "
                "whose status is Completed. Null means no completed request yet."
            ),
        },
        "llm_cost_per_request_eur": {
            "value": llm_cost_per_request,
            "simulation": True,
            "note": "Assumption from the ROI config. Not an invoice.",
        },
        "definitions": {
            "automation_rate": "Completed requests divided by all stored requests.",
            "approval_rate": "Approved decisions divided by approved plus rejected decisions.",
        },
    }


def _iso(value: datetime | None) -> str | None:
    moment = as_utc(value)
    if moment is None:
        return None
    return moment.isoformat()


def _load_tools(raw: str | None) -> list:
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return loaded if isinstance(loaded, list) else []
