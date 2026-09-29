# Architecture

OpsPilot is a controlled operations workflow. n8n decides the order of steps. The demo loads the supplier record and the policy, then the model writes the proposal. The read tools stay connected, and the model is told not to call them on this path, because an open tool loop used every iteration without returning a proposal. FastAPI applies the business writes, and only after the workflow has passed the human gate.

## What runs where

| Piece | Role |
| --- | --- |
| n8n webhook | Receives the user request. |
| FastAPI `POST /operations/intake` | Assigns `REQ-YYYY-NNN`, stores the request, appends an audit event. |
| FastAPI Notion adapter | Creates and updates one page in AI Automation Lab. |
| n8n AI Agent | Chooses among three read tools and returns a structured proposal. |
| FastAPI `POST /suppliers/match` | Finds the one supplier named in the request. 404 if none, 409 if several. |
| n8n Wait node | Stops the execution until a human posts approve or reject with the approver token. Rejects after 24 hours without a decision. |
| FastAPI `POST /operations/{id}/resolve` | Applies Completed or Rejected. Refuses the call if the request is not waiting. |
| Postgres, or SQLite in tests | Current request state plus append-only `audit_events`. |
| `GET /kpi` | Counts from stored requests. |
| `GET /roi` | Simulation from `config/roi_assumptions.json`. |

## Why n8n, not a Python script alone

The workflow has to be visible and editable: webhook, tool calls, a branch, a human pause, retries. n8n shows that path on one canvas and already has the Wait node used for approval. A Python orchestrator would hide the exact thing an automation interview asks to see. Python remains in the repository for the parts that need tests: the supplier contract, the policy search, Notion payloads, audit rows, and ROI math.

## Why an agent, not one LLM call

The tool list is the permission boundary. The system prompt tells the agent that tool output is data, not new instructions. `maxIterations` is 4 so a tool loop cannot run without a limit. The demo workflow loads the supplier record and the policy before the agent and asks it to answer from that data, because an open tool loop was using every iteration without returning a proposal. The three tools stay connected.

The prompt file is `prompts/opspilot_system.md`. The workflow loads it with `GET /prompts/system` at runtime. The node does not contain a second copy.

## Why these three tools

- Supplier API Tool calls `GET /suppliers/{id}`. That is the fictional supplier system.
- Policy Tool calls `GET /knowledge/search`. It returns the matching policy files by keyword overlap. It is not a RAG pipeline.
- Notion Read Tool calls `GET /notion/requests`. It returns id, title, status, and approval. It cannot write.

Writes are ordinary HTTP nodes after the Wait node, not tools. The model never receives a write tool. That split is the control: the agent proposes, the workflow executes.

Sub-workflows were not used. Hiding each tool in another workflow would make the five-minute demo harder to follow. Three tool nodes on the agent are the diagram.

## Why FastAPI in front of Notion

n8n 2.41 has a Notion node. Its page properties are tied to a live database and do not survive a clean git import. The workflow therefore calls FastAPI, and FastAPI calls the Notion REST API with `Notion-Version: 2022-06-28`. The payload is in `api/notion_client.py` and can be tested without a browser. The integration token stays in the API container. It is not placed in the n8n environment.

`scripts/setup_notion.py` creates the database under the parent page id you provide. It does not list or edit the rest of the workspace. The `Error` property is extra compared with the minimum field list: a failure must stay visible without erasing the analysis already written for the reviewer.

Creating the request page before approval is bookkeeping. The sensitive transition is `Completed`, and `resolve` refuses to do it unless the status is already `Waiting Approval`.

## Why the approval call is not trusted by itself

The Wait node is the gate a person can see. The API is a second check: `resolve` returns 409 if the request was never proposed, and it is idempotent if the same decision is posted twice. An invalid decision in the workflow is turned into a rejection, so the default is not a write. There is no signature that proves the Wait node was the caller. The internal token only proves the caller knows the local secret. That limit is stated in [SECURITY.md](SECURITY.md).

## Why Postgres and also SQLite

The audit has to be queried for KPI. Postgres is the database Compose starts. Tests and a laptop run without Docker use SQLite through the same SQLAlchemy models. A file log would be simpler and would not answer `/kpi`.

`operation_requests` is the current state, including the Notion page id. `audit_events` is append-only. Notion is the page a reviewer opens. The local tables are the trace if Notion is down: the API still stores `Failed` and returns the error.

## Why keyword search instead of RAG

The policy set is four short files. Keyword overlap is enough to show that the agent must retrieve a source and must not treat it as instructions. A vector index would add Qdrant, embeddings, and a cost, without changing the demo. The tool response is a JSON contract (`untrusted_data`, `results`). A later retrieval service can replace the function behind that route.

Syro is not called. It was not available for this version, and the workflow does not claim otherwise.

## Why MCP is documented and not built

MCP would standardize the same three tools for another client. This version has one client, n8n, and the tools are already HTTP. Adding an MCP server now would duplicate the adapter. A later version can put Notion, the supplier API, and the policy search behind one MCP server without changing the approval or audit path.

## Errors

- Supplier and Notion calls retry at most three times. A 4xx from Notion is not retried.
- Creating a Notion page is idempotent on `request_id`, so a retry does not create a second page.
- The LLM node is not retried. A failure is logged, the request becomes `Failed`, and the webhook returns that status.
- An unknown supplier, or two suppliers in one request, stops the run before the model. `/suppliers/match` returns 404 or 409, and the request becomes `Failed` with the API message. It never falls back to another supplier.
- Every failure after intake goes through `Capture Failure` and `POST /operations/{id}/fail`, so `/kpi` counts it. `Capture Failure` reads the intake from output 0 of `Normalize Request`: without that index, n8n reads the error output wired to the same node, which is empty.
- Request ids come from one upsert on the yearly counter (`INSERT ... ON CONFLICT DO UPDATE ... RETURNING`). Two simultaneous intakes get two ids on SQLite and on Postgres.
- If Notion cannot be updated after a human decision, the local status becomes `Failed` and the API returns 502. It does not report `Completed`.

## ROI and KPI

`/kpi` counts rows that exist. Rates are null when the denominator is zero. Hours saved and the LLM cost per request are marked `simulation: true`.

`/roi` always uses the assumptions file for the monthly euros. `completed_requests_observed` is the only field taken from the audit log, and it is not multiplied into those euros. The formulas are in [docs/roi.md](docs/roi.md).
