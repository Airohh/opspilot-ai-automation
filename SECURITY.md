# Security

This is a local portfolio system. The controls below are the ones the code actually applies. They are not a production review.

## Secrets

API keys are not committed. `.env` is gitignored. `.env.example` contains empty keys and obvious local placeholders (`change-me-local-only`, `opspilot-local`).

`LLM_API_KEY` is entered in the n8n credential UI. Compose does not pass it into the API container or the n8n environment. n8n encrypts the credential with `N8N_ENCRYPTION_KEY`.

`NOTION_API_KEY` is passed only to the API service. Workflow expressions can read n8n environment variables, so the Notion token and the model key are not put there. The values n8n can read are the API base URL, the model name, and the internal demo token.

## Least privilege

The Notion integration should be invited only to the parent page used by `scripts/setup_notion.py`. The script creates `AI Automation Lab` under that page and stops. It has no search across the workspace.

The agent tools are read endpoints. The model has no tool that creates or updates a page.

## Human approval

`Completed` is set only by `POST /operations/{id}/resolve` with decision `approved`, and only when the current status is `Waiting Approval`. The n8n path to that call goes through the Wait node. A missing or unknown decision is sent as `rejected`.

The approval URL is signed by n8n (a `signature` query parameter) and also requires the `X-OpsPilot-Approver-Token` header. That secret is different from the webhook token, so a requester cannot approve their own request with the secret they submit with. Without a decision within 24 hours, the Wait node resumes and the workflow records a rejection with `approved_by` set to `system:timeout`.

The API does not prove that a human used the Wait node. Anyone who can call the API with `INTERNAL_API_TOKEN` can resolve a waiting request. Keep port 8080 on localhost, and treat the token as a local secret rather than as authentication.

The webhook requires the `X-OpsPilot-Webhook-Token` header through an n8n Header Auth credential. A request without it gets 403 and spends no model call. Compose publishes ports 5678 and 8080 on 127.0.0.1 only.

## Prompt injection

Supplier JSON, policy files, and Notion pages are returned with `untrusted_data: true` where the route is a tool. The system prompt says to treat retrieved text as data and not as instructions. The agent has no write tool, and `maxIterations` limits tool loops. This reduces the impact of an instruction hidden in a page. It does not make prompt injection impossible.

## Data minimization

The supplier route returns the six demonstration fields and nothing else. Notion search returns the request id, title, status, and approval. The model does not receive the Notion token. Logs record the request id and the status, not the full request text.

## Auditability

Each intake, proposal, execution, and failure appends `audit_events`. The event stores the request id, requester, request text, tools, proposal, approval status, approver, execution status, duration, and error. `/audit/{request_id}` returns that trail with the current row.

A Notion failure after a decision is stored as `Failed` with the error message. The API does not answer `Completed` for that case.

## Dependencies and supply chain

Python dependencies are pinned in `api/requirements.txt`. GitHub Actions installs them, runs ruff (lint and format), runs pytest on SQLite and PostgreSQL, and builds the API image. It does not deploy.

## Known gaps

- No user accounts on the API.
- The webhook and approval tokens are shared secrets, not user identities. `approved_by` is what the approver types.
- The internal token ships as a placeholder until you change it.
- Notion API version `2022-06-28` is pinned. If Notion rejects that version, the setup script and the adapter return the error body and do not write a success.
- Policy documents are local files. Replacing them with untrusted web pages would need a stricter size limit than the one used here.
