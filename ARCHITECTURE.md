# Architecture

OpsPilot is a controlled operations workflow. n8n decides the order of steps. The workflow retrieves the supplier record and the policy, then one model call writes the proposal. The model has no tools. FastAPI applies the business writes, and only after the workflow has passed the human gate.

## What runs where

| Piece | Role |
| --- | --- |
| n8n webhook | Receives the user request. |
| FastAPI `POST /operations/intake` | Assigns `REQ-YYYY-NNN`, stores the request, appends an audit event. |
| FastAPI Notion adapter | Creates and updates one page in AI Automation Lab. |
| FastAPI `POST /suppliers/match` | Finds the one supplier named in the request. 404 if none, 409 if several. |
| FastAPI `GET /knowledge/search` | Returns the matching policy files, marked as untrusted data. |
| n8n Draft Proposal (Basic LLM Chain) | Sends the request, the supplier record, and the policy to the model in one call. The reply must match `prompts/proposal_schema.json`. |
| n8n Prepare Proposal | Checks the reply against the same schema. If it does not match, builds a rule-based proposal and sets `fallback_used`. |
| n8n Wait node | Stops the execution until a human posts approve or reject with the approver token. Rejects after 24 hours without a decision. |
| FastAPI `POST /operations/{id}/resolve` | Applies Completed or Rejected. Refuses the call if the request is not waiting. |
| Postgres, or SQLite in tests | Current request state plus append-only `audit_events`. |
| `GET /kpi` | Counts from stored requests. |
| `GET /roi` | Simulation from `config/roi_assumptions.json`. |

## Why n8n, not a Python script alone

The workflow has to be visible and editable: webhook, retrieval, the model call, a branch, a human pause, retries. n8n shows that path on one canvas and already has the Wait node used for approval. A Python orchestrator would hide the exact thing an automation interview asks to see. Python remains in the repository for the parts that need tests: the supplier contract, the policy search, Notion payloads, audit rows, and ROI math.

## Why one model call, not an agent

The first version gave an AI Agent node three read tools (supplier, policy, Notion) and `maxIterations` 4. With `gpt-4o-mini` it spent every iteration calling tools and did not return a proposal. The workflow then loaded the data before the agent and told it not to call the tools, which left three tools connected for show.

The steps for this request type are known in advance: find the supplier, read the payment-terms policy, write a recommendation. Nothing in that path needs the model to choose what to read next. So n8n does the retrieval, and the model does the one step that needs judgment: read the data and write the proposal. That gives one model call per request, a predictable cost, and no tool loop to bound.

An agent fits when the path is not known in advance, for example an open question that could need the supplier, the policy, or past requests in any order. The read endpoints stay in FastAPI for that case. The roadmap puts them behind an MCP server, where the client is the agent.

The prompt file is `prompts/opspilot_system.md` and the output schema is `prompts/proposal_schema.json`. `GET /prompts/system` returns both, and the workflow loads them at runtime. The nodes do not contain a second copy.

## Why the reply is held to a schema

The OpenAI Chat Model node sends `response_format` with `type: json_schema` and `strict: true`, built from the served schema. With OpenAI, the reply must then match the schema. The node uses Chat Completions rather than the Responses API because n8n 2.41 does not pass `strict` on the Responses path and adds a `verbosity` field there.

`Prepare Proposal` checks the reply against the same schema, because a model swapped in for another provider will not enforce it. A reply that is JSON but misses a field gets a rule-based proposal with `fallback_used: true` and an assumption saying so. A reply that is not JSON at all fails the Draft Proposal node, and the request becomes `Failed`.

`tools_used` in the audit lists what the workflow retrieved (`supplier_match`, `policy_search`). It is set by the workflow, not declared by the model.

Writes are ordinary HTTP nodes after the Wait node. The model cannot call anything. That split is the control: the model proposes, the workflow executes.

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

The policy set is four short files. Keyword overlap is enough to show that the workflow retrieves a source and that the model must not treat it as instructions. A vector index would add Qdrant, embeddings, and a cost, without changing the demo. The response is a JSON contract (`untrusted_data`, `results`). A later retrieval service can replace the function behind that route.

Syro is not called. It was not available for this version, and the workflow does not claim otherwise.

## Why MCP is documented and not built

MCP would expose the supplier, policy, and Notion read endpoints as tools to another client, such as Claude Desktop, which would then act as the agent. This version has one client, n8n, and it calls the endpoints directly. A later version can put them behind one MCP server without changing the approval or audit path.

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
