# Live prototype runbook

This runbook takes Agent Work Relay from the credential-free recording
demo to `AWR-GT-001` against a real Cursor Cloud agent.

## What the live test proves

```text
decorated Markdown
→ broker acceptance receipt
→ Cursor Cloud agent in Plan mode
→ durable agent and run acknowledgement
→ terminal Cursor plan
→ fingerprinted PlanPacket
→ ordered receipt ledger
```

Cursor must not edit the repository, push a branch, or open a pull request in
this test.

## Prerequisites

- Python 3.12 and `uv`
- A Cursor Cloud Agents API key
- Cursor's GitHub integration installed for the test repository
- Read access to the repository and its selected base reference

Create the Cursor key in the Cursor Dashboard under **API Keys**. Keep it in a
shell environment or secret manager. Never put it in `.env.example`, a prompt,
the ledger, or Git.

## 1. Prove the broker locally

```bash
uv sync --extra mcp --extra cursor --extra dev
uv run awr demo --db .awr/demo.db
```

The output must contain both a submission receipt and a `PlanPacket`. The local
ledger should end with:

```text
work_order.accepted
work_order.routed
executor.acknowledged
plan.received
plan.available
```

## 2. Configure Cursor Cloud

```bash
export AWR_EXECUTOR=cursor_cloud
export AWR_STORAGE=sqlite
export AWR_SQLITE_PATH=.awr/live.db
export AWR_REPOSITORY_URL=https://github.com/your-org/your-repo
export AWR_BASE_REF=main
export CURSOR_API_BASE_URL=https://api.cursor.com
export CURSOR_API_KEY=your-key
```

The broker sends the key only in Cursor API authentication. It never includes
the key in a work order, wrapper, exception payload, or ledger entry.

## 3. Create a safe test work order

The repository includes `examples/AWR-GT-001.md`:

```markdown
@awr feature.plan

# Inspect the repository

Review this repository and produce an implementation plan for adding a small
health endpoint. Planning only. Do not edit files, commit, push, or open a pull
request.
```

Submit it through the operator CLI:

```bash
uv run awr submit examples/AWR-GT-001.md \
  --sender chatgpt:product-planner \
  --recipient cursor:cloud \
  --idempotency-key AWR-GT-001-live
```

Save the returned `work_order_id`, then wait for the plan:

```bash
uv run awr wait WORK_ORDER_ID --interval 5 --timeout 900
uv run awr ledger --db .awr/live.db --work-order-id WORK_ORDER_ID
```

The `PlanPacket` must contain the same Cursor agent/run IDs recorded by
`executor.acknowledged`, and its SHA-256 must match the returned plan text.

## 4. Prove MCP transport

Start the local stdio server:

```bash
uv run awr mcp
```

The server exposes:

```text
submit_prompt_for_planning
refresh_planning
get_plan
get_work_order_timeline
```

An MCP host should submit the same decorated Markdown, call
`refresh_planning` until the plan is ready, and retrieve the timeline. No
Markdown or result should be manually copied between the planning and coding
tools during this proof.

## Hosted ChatGPT profile

ChatGPT needs an HTTPS Streamable HTTP MCP endpoint rather than this local
stdio process. The hosted profile therefore needs:

- Streamable HTTP transport
- Authentication
- Firestore or Supabase/Postgres as the durable state store
- A secret manager for `CURSOR_API_KEY`
- A scheduler or task queue to refresh active Cursor runs

Do not deploy the SQLite profile to an ephemeral serverless filesystem and call
it durable. The Streamable HTTP MCP resource server, OAuth verification, and
Firestore adapter are implemented in this repository. Live Cloud Run proof still
requires a human-authenticated GCP login, an authorization server, and a Cursor
API key in Secret Manager.

## 5. Run the hosted MCP server locally

```bash
uv sync --extra hosted --extra dev
export AWR_ENV=local
export AWR_AUTH_MODE=static
export AWR_STATIC_TOKEN=local-dev-token
export AWR_STORAGE=memory_firestore
export AWR_PUBLIC_BASE_URL=http://127.0.0.1:43145
uv run awr serve --host 127.0.0.1 --port 43145
```

In another shell:

```bash
./scripts/smoke_test.sh http://127.0.0.1:43145
```

`/healthz` is public. `/mcp` must return `401` with a `WWW-Authenticate`
challenge when no bearer token is present.

## 6. GCP setup and Cloud Run deploy

Use a dedicated, billing-enabled AWR project. This step requires a
human-authenticated `gcloud` session with permission to enable services,
manage the AWR service account and repository, and deploy Cloud Run. The agent
must not receive the Cursor API key.

Preferred path from Google Cloud Shell:

```bash
git clone https://github.com/datateamsix/agent-work-relay.git
cd agent-work-relay

export PROJECT_ID=your-awr-project-id
export REGION=us-central1
export FIRESTORE_DATABASE='(default)'
export FIRESTORE_LOCATION=us-central1
export AWR_REPOSITORY_URL=https://github.com/your-org/your-target-repo
export AWR_BASE_REF=main

./scripts/provision_cloud_run.sh \
  --skip-auth \
  --issuer https://your-tenant.auth0.com/
```

`AWR_REPOSITORY_URL` is the repository Cursor will plan and execute against;
it is not the Git repository containing the broker unless AWR itself is the
intentional test target.

The provisioner requires the project to exist with billing attached. It
creates or verifies a Firestore Native database with delete protection,
creates `awr-runtime`, stores `awr-cursor-api-key`, deploys
`agent-work-relay`, sets `AWR_PUBLIC_BASE_URL` / audience / `AWR_ALLOWED_HOSTS`
to the assigned `*.run.app` URL, imports Firestore indexes, and runs
`scripts/smoke_test.sh` against the public URL. It fails closed when the
project, target repository, OAuth issuer, or Firestore mode is invalid.

Equivalent manual steps:

```bash
export PROJECT_ID=your-awr-project-id
export REGION=us-central1
export FIRESTORE_DATABASE='(default)'
export FIRESTORE_LOCATION=us-central1
export AWR_REPOSITORY_URL=https://github.com/your-org/your-target-repo

gcloud config set project "${PROJECT_ID}"
./deploy/gcp_setup.sh
gcloud secrets versions add awr-cursor-api-key --data-file=-
export AWR_OAUTH_ISSUER=https://your-tenant.auth0.com/
export AWR_OAUTH_AUDIENCE=https://YOUR-SERVICE-URL/mcp
export AWR_PUBLIC_BASE_URL=https://YOUR-SERVICE-URL
export AWR_ALLOWED_HOSTS=YOUR-SERVICE-HOST
./deploy/gcp_deploy.sh
./deploy/apply_firestore_indexes.sh
```

Runtime identity: `awr-runtime@${PROJECT_ID}.iam.gserviceaccount.com`.

Do not grant Vertex AI, BigQuery, Cloud Storage, or project-wide administrative
roles. AWR must remain separate from the PreM3 project and Cloud Run service.

Rollback: `gcloud run services update-traffic agent-work-relay --to-revisions PREVIOUS=100`.

## 7. Connect ChatGPT

1. Complete the Auth0 (or WorkOS) setup in `docs/AUTH.md`.
2. In ChatGPT, add a remote MCP connector pointing at `https://<service>/mcp`.
3. Complete OAuth linking. ChatGPT must use authorization-code + PKCE S256.
4. Submit `examples/AWR-GT-001.md` through `submit_prompt_for_planning`.
5. Call `refresh_planning` until a `PlanPacket` is returned.
6. Confirm `get_work_order_timeline` contains:

```text
work_order.accepted
work_order.routed
executor.acknowledged
plan.received
plan.available
```

Replay the same idempotency key. AWR must return the original work order and
must not create a second Cursor agent.

## Known hosted limitations

- Polling is caller-driven (`refresh_planning`). Cloud Tasks comes later.
- Static tokens are a local-only escape hatch.
- Live GCP deploy still needs a human-authenticated GCP operator, an Auth0
  tenant, and a Secret Manager value that never enters the agent conversation.
