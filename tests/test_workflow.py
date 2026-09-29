import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def nodes() -> dict:
    workflow = json.loads((ROOT / "n8n" / "opspilot-workflow.json").read_text(encoding="utf-8"))
    return {node["name"]: node for node in workflow["nodes"]}


def test_webhook_requires_a_header_token(nodes):
    webhook = nodes["Webhook"]
    assert webhook["parameters"]["authentication"] == "headerAuth"
    assert "httpHeaderAuth" in webhook["credentials"]


def test_approval_uses_its_own_token_and_expires(nodes):
    wait = nodes["Wait for Human Approval"]
    params = wait["parameters"]
    assert params["incomingAuthentication"] == "headerAuth"
    requester = nodes["Webhook"]["credentials"]["httpHeaderAuth"]["name"]
    assert wait["credentials"]["httpHeaderAuth"]["name"] != requester
    assert params["limitWaitTime"] is True
    assert (params["resumeAmount"], params["resumeUnit"]) == (24, "hours")
    decision = nodes["Read Approval Decision"]["parameters"]["jsCode"]
    assert "system:timeout" in decision


def test_supplier_is_resolved_by_the_api(nodes):
    params = nodes["Load Supplier"]["parameters"]
    assert params["url"].endswith("/suppliers/match' }}")
    for supplier_id in ("ACME", "NORDIC", "HELIX"):
        assert supplier_id not in json.dumps(params)


def test_agent_tools_are_read_only(nodes):
    tools = [node for node in nodes.values() if node["type"].endswith("httpRequestTool")]
    assert len(tools) == 3
    assert {tool["parameters"]["method"] for tool in tools} == {"GET"}


def test_failure_path_reads_the_intake_output(nodes):
    code = nodes["Capture Failure"]["parameters"]["jsCode"]
    assert "$('Normalize Request').first(0)" in code
