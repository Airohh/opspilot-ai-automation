"""OpsPilot business API.

n8n orchestrates the agent and the human approval. This service owns supplier data,
policy search, the Notion page, the audit trail, and the ROI simulation.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db import (
    AuditEvent,
    OperationRequest,
    add_event,
    build_kpi,
    execution_time_ms,
    init_db,
    make_engine,
    next_request_id,
    utcnow,
)
from knowledge import KnowledgeBase
from notion_client import NotionClient, NotionError, notion_properties
from roi import calculate_roi, load_assumptions
from suppliers import SupplierStore

logger = logging.getLogger("opspilot")
REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_env_file(REPO_ROOT / ".env")


def _choose(explicit: str | None, env_name: str, default: str) -> str:
    if explicit is not None:
        return explicit
    return os.getenv(env_name, default)


def _choose_path(explicit: Path | None, env_name: str, default: Path) -> Path:
    if explicit is not None:
        return explicit
    raw = os.getenv(env_name)
    if raw:
        return Path(raw)
    return default


class IntakeIn(BaseModel):
    request: str = Field(min_length=1)
    requester: str = Field(default="portfolio-user", min_length=1)

    @field_validator("request", "requester")
    @classmethod
    def must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class ProposalIn(BaseModel):
    request_summary: str = ""
    information_consulted: list[str] = []
    facts: list[str] = []
    assumptions: list[str] = []
    analysis: str = ""
    proposed_action: str = ""
    expected_impact: str = ""
    requires_human_approval: bool = True
    clarification_needed: bool = False
    tools_used: list[str] = []
    fallback_used: bool = False


class ResolveIn(BaseModel):
    decision: str
    approved_by: str = Field(min_length=1)
    error_message: str | None = None

    @field_validator("decision")
    @classmethod
    def known_decision(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in {"approved", "rejected"}:
            raise ValueError("decision must be approved or rejected")
        return cleaned

    @field_validator("approved_by")
    @classmethod
    def approver_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class FailIn(BaseModel):
    error_message: str = Field(min_length=1)

    @field_validator("error_message")
    @classmethod
    def message_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class NotionLinkIn(BaseModel):
    request_id: str = Field(min_length=1)


class SupplierMatchIn(BaseModel):
    text: str = Field(min_length=1)


def create_app(
    *,
    database_url: str | None = None,
    internal_token: str | None = None,
    knowledge_dir: Path | None = None,
    prompt_path: Path | None = None,
    assumptions_path: Path | None = None,
    suppliers_path: Path | None = None,
    notion_api_key: str | None = None,
    notion_database_id: str | None = None,
) -> FastAPI:
    database_url = _choose(database_url, "DATABASE_URL", "sqlite:///./opspilot.db")
    internal_token = _choose(internal_token, "INTERNAL_API_TOKEN", "")
    knowledge_dir = _choose_path(knowledge_dir, "KNOWLEDGE_DIR", REPO_ROOT / "knowledge")
    prompt_path = _choose_path(
        prompt_path, "PROMPT_PATH", REPO_ROOT / "prompts" / "opspilot_system.md"
    )
    assumptions_path = _choose_path(
        assumptions_path,
        "ROI_ASSUMPTIONS_PATH",
        REPO_ROOT / "config" / "roi_assumptions.json",
    )
    suppliers_path = suppliers_path or Path(__file__).parent / "data" / "suppliers.json"
    notion_api_key = _choose(notion_api_key, "NOTION_API_KEY", "")
    notion_database_id = _choose(notion_database_id, "NOTION_DATABASE_ID", "")

    engine = make_engine(database_url)
    session_factory = init_db(engine)
    assumptions = load_assumptions(assumptions_path)
    knowledge = KnowledgeBase(knowledge_dir)
    supplier_store = SupplierStore(suppliers_path)
    prompt_text = prompt_path.read_text(encoding="utf-8")
    notion = None
    if notion_api_key and notion_database_id:
        notion = NotionClient(notion_api_key, notion_database_id)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if app.state.notion is not None:
            app.state.notion.close()

    app = FastAPI(title="OpsPilot API", version="1", lifespan=lifespan)
    app.state.session_factory = session_factory
    app.state.internal_token = internal_token
    app.state.assumptions = assumptions
    app.state.knowledge = knowledge
    app.state.suppliers = supplier_store
    app.state.prompt_text = prompt_text
    app.state.notion = notion
    app.state.notion_api_key = notion_api_key
    app.state.notion_database_id = notion_database_id

    @app.get("/health")
    def health(request: Request) -> dict:
        state = request.app.state
        return {
            "status": "ok",
            "auth": "enabled" if state.internal_token else "disabled",
            "notion": "configured" if state.notion else "not_configured",
        }

    @app.get("/suppliers/{supplier_id}")
    def get_supplier(supplier_id: str, request: Request, _: None = Depends(require_token)) -> dict:
        try:
            found = request.app.state.suppliers.get(supplier_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if found is None:
            raise HTTPException(status_code=404, detail="Supplier not found")
        return {"demonstration_data": True, **found}

    @app.get("/suppliers")
    def search_suppliers(
        request: Request,
        q: str = "",
        _: None = Depends(require_token),
    ) -> dict:
        return {
            "demonstration_data": True,
            "results": request.app.state.suppliers.search(q),
        }

    @app.post("/suppliers/match")
    def match_supplier(
        body: SupplierMatchIn,
        request: Request,
        _: None = Depends(require_token),
    ) -> dict:
        matches = request.app.state.suppliers.match(body.text)
        if not matches:
            raise HTTPException(
                status_code=404,
                detail="No known supplier is named in the request. Ask the requester which one.",
            )
        if len(matches) > 1:
            names = ", ".join(item["id"] for item in matches)
            raise HTTPException(
                status_code=409,
                detail=f"Several suppliers are named ({names}). Ask the requester which one.",
            )
        return {"demonstration_data": True, **matches[0]}

    @app.get("/knowledge/search")
    def search_knowledge(
        request: Request,
        q: str,
        _: None = Depends(require_token),
    ) -> dict:
        if not q.strip():
            raise HTTPException(status_code=422, detail="Query is required")
        return request.app.state.knowledge.search(q)

    @app.get("/prompts/system")
    def system_prompt(request: Request, _: None = Depends(require_token)) -> dict:
        return {"version": "1", "content": request.app.state.prompt_text}

    @app.post("/operations/intake")
    def intake(
        body: IntakeIn,
        session: Session = Depends(get_session),
        _: None = Depends(require_token),
    ) -> dict:
        now = utcnow()
        request_id = next_request_id(session, now)
        row = OperationRequest(
            request_id=request_id,
            requester=body.requester,
            request_text=body.request,
            status="Processing",
            human_approval="Pending",
            created_at=now,
        )
        session.add(row)
        add_event(session, row, "intake", execution_status="processing")
        logger.info("intake %s", request_id)
        return {
            "request_id": request_id,
            "timestamp": now.isoformat(),
            "requester": body.requester,
            "request": body.request,
        }

    @app.post("/notion/requests")
    def create_notion_request(
        body: NotionLinkIn,
        session: Session = Depends(get_session),
        notion_client: NotionClient = Depends(get_notion),
        _: None = Depends(require_token),
    ) -> dict:
        row = _get_request(session, body.request_id)
        if row.notion_page_id:
            return {
                "request_id": row.request_id,
                "notion_page_id": row.notion_page_id,
                "status": row.status,
            }
        try:
            page_id = notion_client.create_request(
                row.request_id,
                row.request_text,
                row.requester,
                row.created_at.isoformat(),
            )
        except NotionError as exc:
            _mark_failed(session, row, str(exc))
            session.commit()
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        row.notion_page_id = page_id
        add_event(session, row, "notion_created", execution_status="processing")
        return {
            "request_id": row.request_id,
            "notion_page_id": page_id,
            "status": row.status,
        }

    @app.get("/notion/requests")
    def search_notion(
        q: str = "",
        notion_client: NotionClient = Depends(get_notion),
        _: None = Depends(require_token),
    ) -> dict:
        try:
            return notion_client.search(q)
        except NotionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @app.post("/operations/{request_id}/proposal")
    def record_proposal(
        request_id: str,
        body: ProposalIn,
        session: Session = Depends(get_session),
        notion_client: NotionClient = Depends(get_notion),
        _: None = Depends(require_token),
    ) -> dict:
        row = _get_request(session, request_id)
        if row.status == "Waiting Approval":
            return row.to_dict()
        if row.status != "Processing":
            raise HTTPException(status_code=409, detail=f"Cannot propose from status {row.status}")
        if not row.notion_page_id:
            raise HTTPException(status_code=409, detail="Notion page is missing")
        needs_text = not body.analysis.strip() or not body.proposed_action.strip()
        if not body.clarification_needed and needs_text:
            raise HTTPException(
                status_code=422,
                detail="analysis and proposed_action are required unless clarification is needed",
            )
        row.ai_analysis = _format_analysis(body)
        row.proposed_action = body.proposed_action or body.request_summary
        row.expected_impact = body.expected_impact
        row.tools_used = json.dumps(body.tools_used)
        row.fallback_used = body.fallback_used
        row.status = "Waiting Approval"
        row.human_approval = "Pending"
        row.waiting_approval_at = utcnow()
        try:
            notion_client.update_request(
                row.notion_page_id,
                notion_properties(
                    status="Waiting Approval",
                    human_approval="Pending",
                    ai_analysis=row.ai_analysis,
                    proposed_action=row.proposed_action,
                ),
            )
        except NotionError as exc:
            _mark_failed(session, row, str(exc))
            session.commit()
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        add_event(
            session,
            row,
            "proposal",
            execution_status="waiting_approval",
            agent_decision=body.analysis or body.request_summary,
        )
        logger.info("proposal %s", request_id)
        return row.to_dict()

    @app.post("/operations/{request_id}/resolve")
    def resolve_request(
        request_id: str,
        body: ResolveIn,
        session: Session = Depends(get_session),
        notion_client: NotionClient = Depends(get_notion),
        _: None = Depends(require_token),
    ) -> dict:
        row = _get_request(session, request_id)
        approved = body.decision == "approved"
        target_status = "Completed" if approved else "Rejected"
        target_approval = "Approved" if approved else "Rejected"
        if row.status == target_status and row.human_approval == target_approval:
            return row.to_dict()
        if row.status != "Waiting Approval":
            raise HTTPException(status_code=409, detail=f"Cannot resolve from status {row.status}")
        if not row.notion_page_id:
            raise HTTPException(status_code=409, detail="Notion page is missing")
        now = utcnow()
        try:
            notion_client.update_request(
                row.notion_page_id,
                notion_properties(
                    status=target_status,
                    human_approval=target_approval,
                    processed_at=now.isoformat(),
                    error_message=body.error_message,
                ),
            )
        except NotionError as exc:
            _mark_failed(session, row, str(exc))
            session.commit()
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        row.human_approval = target_approval
        row.approved_by = body.approved_by
        row.status = target_status
        row.processed_at = now
        if body.error_message:
            row.error_message = body.error_message
        elapsed = execution_time_ms(row, now)
        add_event(
            session,
            row,
            "execution",
            execution_status="completed" if approved else "rejected",
            execution_time_ms=elapsed,
            agent_decision=row.ai_analysis,
        )
        logger.info("resolve %s %s", request_id, target_status)
        return row.to_dict()

    @app.post("/operations/{request_id}/fail")
    def fail_request(
        request_id: str,
        body: FailIn,
        http_request: Request,
        session: Session = Depends(get_session),
        _: None = Depends(require_token),
    ) -> dict:
        row = _get_request(session, request_id)
        if row.status == "Failed":
            return row.to_dict()
        if row.status in {"Completed", "Rejected"}:
            raise HTTPException(status_code=409, detail=f"Cannot fail a {row.status} request")
        _mark_failed(session, row, body.error_message)
        notion_error = _sync_notion_failure(http_request.app.state.notion, row)
        logger.info("fail %s", request_id)
        payload = row.to_dict()
        if notion_error:
            payload["notion_error"] = notion_error
        return payload

    @app.get("/audit/{request_id}")
    def audit_trail(
        request_id: str,
        session: Session = Depends(get_session),
        _: None = Depends(require_token),
    ) -> dict:
        row = _get_request(session, request_id)
        events = session.scalars(
            select(AuditEvent).where(AuditEvent.request_id == request_id).order_by(AuditEvent.id)
        ).all()
        return {"request": row.to_dict(), "events": [event.to_dict() for event in events]}

    @app.get("/kpi")
    def kpi(
        request: Request,
        session: Session = Depends(get_session),
        _: None = Depends(require_token),
    ):
        assumptions = request.app.state.assumptions
        saved = (
            assumptions["manual_minutes_per_request"]
            - assumptions["automated_review_minutes_per_request"]
        )
        return build_kpi(session, assumptions["llm_cost_per_request_eur"], saved)

    @app.get("/roi")
    def roi(
        request: Request,
        session: Session = Depends(get_session),
        _: None = Depends(require_token),
    ):
        completed = session.scalar(
            select(func.count())
            .select_from(OperationRequest)
            .where(OperationRequest.status == "Completed")
        )
        return calculate_roi(request.app.state.assumptions, int(completed or 0))

    return app


def get_session(request: Request):
    session = request.app.state.session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def require_token(
    request: Request,
    x_opspilot_token: str | None = Header(default=None),
) -> None:
    expected = request.app.state.internal_token
    if not expected:
        return
    if x_opspilot_token != expected:
        raise HTTPException(status_code=401, detail="Missing or invalid X-OpsPilot-Token")


def get_notion(request: Request) -> NotionClient:
    client = request.app.state.notion
    if client is None:
        missing = []
        if not request.app.state.notion_api_key:
            missing.append("NOTION_API_KEY")
        if not request.app.state.notion_database_id:
            missing.append("NOTION_DATABASE_ID")
        raise HTTPException(
            status_code=503,
            detail={"error": "Notion is not configured", "missing": missing},
        )
    return client


def _get_request(session: Session, request_id: str) -> OperationRequest:
    row = session.get(OperationRequest, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return row


def _mark_failed(session: Session, row: OperationRequest, message: str) -> None:
    row.status = "Failed"
    row.error_message = message
    row.processed_at = utcnow()
    add_event(
        session,
        row,
        "error",
        execution_status="failed",
        execution_time_ms=execution_time_ms(row, row.processed_at),
    )


def _format_analysis(body: ProposalIn) -> str:
    consulted = ", ".join(body.information_consulted) or "none"
    facts = "; ".join(body.facts) or "none"
    assumptions = "; ".join(body.assumptions) or "none"
    summary = body.request_summary or "none"
    analysis = body.analysis or "Clarification required."
    impact = body.expected_impact or "none"
    return (
        f"Summary: {summary}\n"
        f"Consulted: {consulted}\n"
        f"Facts: {facts}\n"
        f"Assumptions: {assumptions}\n"
        f"Analysis: {analysis}\n"
        f"Expected impact: {impact}"
    )


def _sync_notion_failure(notion_client: NotionClient | None, row: OperationRequest) -> str | None:
    if notion_client is None or not row.notion_page_id:
        return None
    try:
        notion_client.update_request(
            row.notion_page_id,
            notion_properties(status="Failed", error_message=row.error_message),
        )
    except NotionError as exc:
        return str(exc)
    return None


app = create_app()
