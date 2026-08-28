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
it durable. The hosted profile is the next deployment milestone after the live
Cursor round trip passes.
