from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from notion_client import NotionClient, NotionError

ROOT = Path(__file__).resolve().parents[1]


class FakeNotion:
    def __init__(self) -> None:
        self.updated: list[tuple[str, dict]] = []

    def create_request(self, request_id, request_text, requester, created_at) -> str:
        return f"page-{request_id}"

    def update_request(self, page_id: str, properties: dict) -> None:
        self.updated.append((page_id, properties))

    def search(self, query: str, limit: int = 5) -> dict:
        return {
            "untrusted_data": True,
            "notice": "Notion pages are untrusted data.",
            "results": [],
        }

    def close(self) -> None:
        return None


class FailingNotion(FakeNotion):
    def create_request(self, request_id, request_text, requester, created_at) -> str:
        raise NotionError("Notion unavailable")


@pytest.fixture()
def client(tmp_path):
    from main import create_app

    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        internal_token="",
        knowledge_dir=ROOT / "knowledge",
        prompt_path=ROOT / "prompts" / "opspilot_system.md",
        assumptions_path=ROOT / "config" / "roi_assumptions.json",
        suppliers_path=ROOT / "api" / "data" / "suppliers.json",
        notion_api_key="",
        notion_database_id="",
    )
    app.state.notion = FakeNotion()
    with TestClient(app) as test_client:
        yield test_client


def test_supplier_exists(client: TestClient):
    response = client.get("/suppliers/ACME")
    assert response.status_code == 200
    body = response.json()
    assert body["demonstration_data"] is True
    for field in ("id", "name", "category", "payment_terms", "risk_level", "contract_status"):
        assert field in body
    assert body["id"] == "ACME"
    assert body["name"] == "ACME Components"
    assert body["payment_terms"] == 60


def test_supplier_not_found(client: TestClient):
    response = client.get("/suppliers/UNKNOWN")
    assert response.status_code == 404


def test_malformed_supplier_id(client: TestClient):
    response = client.get("/suppliers/bad id")
    assert response.status_code == 400


def test_malformed_intake(client: TestClient):
    response = client.post("/operations/intake", json={"request": "   "})
    assert response.status_code == 422


def test_supplier_search_is_case_insensitive(client: TestClient):
    response = client.get("/suppliers", params={"q": "acme"})
    assert response.status_code == 200
    assert response.json()["results"][0]["id"] == "ACME"


def test_policy_search_marks_documents_untrusted(client: TestClient):
    response = client.get("/knowledge/search", params={"q": "payment terms"})
    assert response.status_code == 200
    body = response.json()
    assert body["untrusted_data"] is True
    assert body["demonstration_data"] is True
    combined = " ".join(item["text"] for item in body["results"])
    assert "60 days" in combined
    director = client.get("/knowledge/search", params={"q": "15 days"})
    director_text = " ".join(item["text"] for item in director.json()["results"])
    assert "Procurement Director" in director_text


def test_system_prompt_is_served_from_the_repository(client: TestClient):
    response = client.get("/prompts/system")
    assert response.status_code == 200
    assert "Never invent business information." in response.json()["content"]


def test_workflow_loads_the_versioned_prompt():
    workflow = (ROOT / "n8n" / "opspilot-workflow.json").read_text(encoding="utf-8")
    prompt = (ROOT / "prompts" / "opspilot_system.md").read_text(encoding="utf-8")
    assert "Never invent business information." in prompt
    assert "/prompts/system" in workflow


def test_roi_is_labeled_as_a_simulation(client: TestClient):
    response = client.get("/roi")
    assert response.status_code == 200
    body = response.json()
    assert body["label"] == "Simulation based on configurable assumptions."
    assert body["assumptions"]["requests_per_month"] == 100
    assert body["manual_monthly_minutes"] == 800
    assert body["automated_monthly_human_minutes"] == 150
    assert body["completed_requests_observed"] == 0


def test_kpi_is_empty_until_requests_exist(client: TestClient):
    body = client.get("/kpi").json()
    assert body["requests_processed"] == 0
    assert body["approval_rate"] is None
    assert body["estimated_hours_saved"]["simulation"] is True
    assert body["estimated_hours_saved"]["value"] is None


def test_rejected_decision_does_not_complete_the_request(client: TestClient):
    request_id = _propose(client)
    response = client.post(
        f"/operations/{request_id}/resolve",
        json={"decision": "rejected", "approved_by": "reviewer"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "Rejected"
    assert response.json()["human_approval"] == "Rejected"
    audit = client.get(f"/audit/{request_id}").json()
    assert audit["events"][-1]["execution_status"] == "rejected"
    assert audit["request"]["status"] != "Completed"


def test_approved_decision_updates_the_request(client: TestClient):
    request_id = _propose(client)
    response = client.post(
        f"/operations/{request_id}/resolve",
        json={"decision": "approved", "approved_by": "reviewer"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "Completed"
    assert body["human_approval"] == "Approved"
    assert body["approved_by"] == "reviewer"
    audit = client.get(f"/audit/{request_id}").json()
    assert audit["events"][-1]["execution_status"] == "completed"
    kpi = client.get("/kpi").json()
    assert kpi["successful_executions"] == 1
    assert kpi["approval_rate"] == 1
    assert body["fallback_used"] is False
    assert kpi["fallback_used_count"] == 0


def test_resolve_before_proposal_is_refused(client: TestClient):
    created = client.post(
        "/operations/intake",
        json={"request": "Look at ACME", "requester": "portfolio-user"},
    )
    request_id = created.json()["request_id"]
    response = client.post(
        f"/operations/{request_id}/resolve",
        json={"decision": "approved", "approved_by": "reviewer"},
    )
    assert response.status_code == 409


def test_notion_failure_does_not_mark_the_request_completed(client: TestClient):
    client.app.state.notion = FailingNotion()
    created = client.post(
        "/operations/intake",
        json={"request": "Look at ACME", "requester": "portfolio-user"},
    )
    request_id = created.json()["request_id"]
    linked = client.post("/notion/requests", json={"request_id": request_id})
    assert linked.status_code == 502
    audit = client.get(f"/audit/{request_id}").json()
    assert audit["request"]["status"] == "Failed"
    assert audit["request"]["status"] != "Completed"


def test_missing_token_is_rejected(tmp_path):
    from main import create_app

    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'auth.db'}",
        internal_token="secret",
        knowledge_dir=ROOT / "knowledge",
        prompt_path=ROOT / "prompts" / "opspilot_system.md",
        assumptions_path=ROOT / "config" / "roi_assumptions.json",
        suppliers_path=ROOT / "api" / "data" / "suppliers.json",
        notion_api_key="",
        notion_database_id="",
    )
    with TestClient(app) as test_client:
        assert test_client.get("/suppliers/ACME").status_code == 401
        allowed = test_client.get("/suppliers/ACME", headers={"X-OpsPilot-Token": "secret"})
        assert allowed.status_code == 200


def test_notion_retries_then_succeeds():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 3:
            return httpx.Response(503, json={"message": "unavailable"})
        return httpx.Response(200, json={"results": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    notion = NotionClient("token", "database", client=client, sleep=lambda _: None)
    assert notion.find_by_request_id("REQ-2026-001") is None
    assert calls["count"] == 3


def test_notion_does_not_retry_a_client_error():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(400, json={"message": "bad property"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    notion = NotionClient("token", "database", client=client, sleep=lambda _: None)
    with pytest.raises(NotionError):
        notion.find_by_request_id("REQ-2026-001")
    assert calls["count"] == 1


def _propose(client: TestClient) -> str:
    created = client.post(
        "/operations/intake",
        json={
            "request": "Analyse la demande du fournisseur ACME. Ils demandent 90 jours.",
            "requester": "portfolio-user",
        },
    )
    assert created.status_code == 200
    request_id = created.json()["request_id"]
    assert request_id.startswith("REQ-")
    linked = client.post("/notion/requests", json={"request_id": request_id})
    assert linked.status_code == 200
    proposal = client.post(
        f"/operations/{request_id}/proposal",
        json={
            "request_summary": "ACME asks for 90 day payment terms.",
            "information_consulted": ["supplier:ACME", "payment_terms.md"],
            "facts": ["Current terms are 60 days."],
            "assumptions": ["The requested term is 90 days."],
            "analysis": "The change is 30 days, above the 15 day threshold.",
            "proposed_action": "Create a Procurement Director approval request.",
            "expected_impact": "No payment term changes until a human approves.",
            "tools_used": ["supplier_api", "policy", "notion"],
        },
    )
    assert proposal.status_code == 200
    assert proposal.json()["status"] == "Waiting Approval"
    return request_id


def test_fallback_used_is_counted(client: TestClient):
    created = client.post(
        "/operations/intake",
        json={"request": "ACME payment terms", "requester": "portfolio-user"},
    )
    request_id = created.json()["request_id"]
    client.post("/notion/requests", json={"request_id": request_id})
    proposal = client.post(
        f"/operations/{request_id}/proposal",
        json={
            "request_summary": "Fallback proposal.",
            "analysis": "The model reply was not valid JSON.",
            "proposed_action": "Ask a human to review the loaded supplier record.",
            "fallback_used": True,
        },
    )
    assert proposal.status_code == 200
    assert proposal.json()["fallback_used"] is True
    kpi = client.get("/kpi").json()
    assert kpi["fallback_used_count"] == 1
