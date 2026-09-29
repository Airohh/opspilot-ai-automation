# OpsPilot

OpsPilot is a local AI operations assistant. A person sends an operational request. n8n orchestrates an LLM agent, the agent reads only the tools it is given, and nothing sensitive is written until a human approves it. Notion stores the business request. FastAPI exposes the supplier data, the policy files, the audit log, and a ROI simulation.

Supplier records and policy text in this repository are fictional demonstration data.

## Business problem

An operations team receives requests such as "ACME wants 90-day payment terms instead of 60." Someone has to find the supplier, read the policy, write a recommendation, and wait for approval. The repetitive reading can be prepared by an agent. The decision stays with a person.

## Demo scenario

Request:

> Analyse la demande du fournisseur ACME concernant ses conditions de paiement. Ils demandent 90 jours au lieu de 60.

Expected path:

1. The webhook receives the request and FastAPI assigns an id such as `REQ-2026-001`.
2. A Notion page is created with status `Processing`.
3. The workflow loads the ACME supplier record (60 days, fictional demonstration data) and the payment-terms policy, then passes both to the agent.
4. The agent returns a proposal. A change above 15 days needs Procurement Director approval. It does not change any payment term. The supplier, policy, and Notion read tools stay attached. This demo loads the records first so the model answers instead of looping on tool calls.
5. The webhook response contains the analysis and an approval URL. Notion moves to `Waiting Approval`.
6. A human posts `approved` or `rejected` to that URL.
7. Approval sets the Notion page to `Completed`. Rejection sets it to `Rejected` and does not apply the change.
8. `GET /audit/{request_id}` shows the trail. `GET /kpi` shows measured counts. `GET /roi` shows a labeled simulation.

## Architecture

```mermaid
flowchart TD
  user[User] --> webhook[n8n Webhook]
  webhook --> api[FastAPI]
  api --> notion[Notion AI Automation Lab]
  webhook --> agent[AI Agent]
  agent --> supplier[Supplier API]
  agent --> policy[Policy files]
  agent --> notionRead[Notion read]
  agent --> proposal[Proposed action]
  proposal --> human[Human approval]
  human -->|rejected| auditReject[Audit rejection]
  human -->|approved| notionWrite[Notion update]
  notionWrite --> audit[Audit log]
  audit --> kpi[KPI]
  kpi --> roi[ROI simulation]
```

n8n is the orchestration layer. FastAPI is the business adapter. The reason for each choice is in [ARCHITECTURE.md](ARCHITECTURE.md).

## Tech stack

- n8n 2.41.3, AI Agent node, webhook, Wait node
- FastAPI and Python 3.12
- PostgreSQL 16 for the audit log in Docker; SQLite for tests
- Notion API, one database created for this POC
- An OpenAI-compatible chat model configured in n8n
- Docker Compose
- GitHub Actions: tests, ruff, API image build

Not used in this version: Syro, a vector database, MCP, Slack, a frontend, cloud deployment.

## Installation

Docker Desktop is required. Copy the environment file and edit it:

```powershell
Copy-Item .env.example .env
```

```bash
cp .env.example .env
```

Create a Notion integration and connect it only to one empty parent page. Put the token and that page id in `.env`, then create the database:

```powershell
python scripts/setup_notion.py
```

Copy the printed `NOTION_DATABASE_ID` into `.env`.

Stop the practice n8n container if it is already using port 5678:

```powershell
docker stop n8n
docker rm n8n
```

That container is separate from Compose. Compose uses its own volume.

## Environment variables

| Variable | Who reads it | Purpose |
| --- | --- | --- |
| `LLM_API_KEY` | You, in the n8n credential screen | Model provider key. Not injected into containers. |
| `LLM_MODEL` | n8n | Model id, default `gpt-4o-mini`. |
| `NOTION_API_KEY` | FastAPI | Integration token for the POC database only. |
| `NOTION_PARENT_PAGE_ID` | `scripts/setup_notion.py` | Parent of the new database. |
| `NOTION_DATABASE_ID` | FastAPI | Database created by the setup script. |
| `INTERNAL_API_TOKEN` | n8n and FastAPI | Local token on the API calls. |
| `POSTGRES_PASSWORD` | Postgres and FastAPI | Local database password. |
| `DATABASE_URL` | FastAPI outside Compose | Overridden inside Compose. |
| `N8N_ENCRYPTION_KEY` | n8n | Keeps credentials stable across restarts. |
| `SUPPLIER_API_URL` | Documentation | Base URL of this API. |

## How to run

From this folder, with `.env` present:

```powershell
docker compose up --build
```

- n8n: http://localhost:5678
- API health: http://localhost:8080/health

On the first n8n screen, create a local owner account. It is not an n8n Cloud account.

Then, in n8n:

1. Create an OpenAI credential with `LLM_API_KEY`. The workflow node is the OpenAI chat model. Swapping that node is how you use Anthropic or Mistral instead.
2. Import `n8n/opspilot-workflow.json`.
3. Open **OpenAI Chat Model** and select that credential.
4. Activate **OpsPilot — AI Operations Workflow**.

## How to test

Automated:

```powershell
python -m pip install -r api/requirements.txt -r api/requirements-dev.txt
python -m pytest
```

Manual, after Compose is up and the workflow is active. In PowerShell, call `curl.exe`, not `curl`.

### Test 1 — normal request

```powershell
curl.exe -X POST http://localhost:5678/webhook/opspilot `
  -H "Content-Type: application/json" `
  -d "{\"request\":\"Analyse la demande du fournisseur ACME concernant ses conditions de paiement. Ils demandent 90 jours au lieu de 60.\",\"requester\":\"portfolio-user\"}"
```

Expected: supplier and policy are consulted, a proposal is returned, `approval_url` is present, Notion is `Waiting Approval`. No payment term is changed.

### Test 2 — rejected

Post this JSON to `approval_url`:

```json
{"decision":"rejected","approved_by":"portfolio-user"}
```

Expected: Notion becomes `Rejected`. The audit execution status is `rejected`. The request is not `Completed`.

### Test 3 — approved

Use a new webhook call, then:

```json
{"decision":"approved","approved_by":"portfolio-user"}
```

Expected: Notion becomes `Completed`, the analysis and proposed action are on the page, and the audit execution status is `completed`.

### Test 4 — API unavailable

Stop the API container and send the webhook again.

```powershell
docker stop opspilot-api-1
```

The Compose service name is `api`; the container name is usually `opspilot-api-1`.

Expected: the HTTP nodes retry, then the webhook returns `Failed`. No request is marked `Completed`.

```powershell
docker start opspilot-api-1
```

KPI and ROI, from the host:

```powershell
curl.exe -H "X-OpsPilot-Token: change-me-local-only" http://localhost:8080/kpi
curl.exe -H "X-OpsPilot-Token: change-me-local-only" http://localhost:8080/roi
python scripts/roi.py
```

Replace the token if you changed `INTERNAL_API_TOKEN`.

## Example workflow

The importable workflow is [n8n/opspilot-workflow.json](n8n/opspilot-workflow.json).

Webhook → Normalize Request → Create Notion Request → Load System Prompt → Load Supplier → Load Policy → AI Agent → Prepare Proposal → Record Proposal → Respond With Proposal → Wait for Human Approval → Approved? → Execute Approved Action or Record Rejection.

The agent tools are Supplier API Tool, Policy Tool, and Notion Read Tool. The system prompt is loaded from `prompts/opspilot_system.md` through `GET /prompts/system`, so the file in git is the copy the agent receives.

## Security considerations

Details and the risks that are still open are in [SECURITY.md](SECURITY.md).

Short version: keys stay in `.env` or in the n8n credential store. The Notion token is limited to the POC page. Write completion happens only after the Wait node. Tool output is marked untrusted. The audit log records the decision and the outcome.

## ROI methodology

The calculation is a simulation. The label returned by the API is `Simulation based on configurable assumptions.` Assumptions live in [config/roi_assumptions.json](config/roi_assumptions.json). The formulas are in [docs/roi.md](docs/roi.md).

Measured counts come from `/kpi`. They are not mixed into the monthly euro figures. Those euro figures use `requests_per_month` from the assumptions file.

## Limitations

- The human gate is an n8n Wait webhook, not a product UI.
- The executed action updates the Notion request. It does not call an ERP.
- There is no login on the public webhook. Keep it on localhost.
- FastAPI trusts n8n once the internal token matches. The token is a local demo value.
- Policy search is keyword overlap, not retrieval with embeddings.
- Request ids are a yearly counter without a lock. Two simultaneous intakes could collide.
- LLM calls are not retried, because each retry costs money. Notion and HTTP calls are retried a bounded number of times.
- Syro, MCP, Slack, and cloud deployment are not implemented.

## Roadmap

- Replace the keyword policy tool with an HTTP call to a retrieval API, without changing the agent contract.
- Move the three tools behind an MCP server once more than one agent needs them.
- Add Slack as a second approval channel in front of the same Wait contract.
- Add webhook header authentication before any host other than localhost is used.
- Deploy the Compose stack only after the internal token and the Notion token are real secrets.
