@response
---
awr:
  schema: awr.response/v1
  response_type: execution.completed
  work_order_id: <work-order-id>
  in_reply_to: <ack-or-progress-id>
  idempotency_key: awr:<work-order-id>:execution.completed:v1
  source_input_sha256: sha256:<digest>
  created_at: <rfc3339-timestamp>
  authority: report_only
  executor_run_id: <bound-provider-run-id>
  content_sha256: sha256:<canonical-packet-digest>
---

# Execution completed: <title>

Required protocol fields are in the envelope. Required lifecycle bindings:
bound run ID and source fingerprint. Do not paste complete logs or diffs.

## Outcome

<What is now true.>

## Changes

- `<path or contract>` — <change and reason>

## Acceptance criteria

| Criterion | Result | Evidence |
|---|---|---|
| <criterion> | PASS / FAIL / NOT VERIFIED | <command, test, or artifact ID> |

## Verification

| Command or check | Result |
|---|---|
| `<command>` | <result> |

## Git and delivery references

- Repository: <URL>
- Base commit: <SHA>
- Final commit: <SHA>
- Branch: <name>
- Pull request: <URL or none>

## Migrations and operations

- <Migration or none. Never include secret values.>

## Deviations, residual risk, and follow-up

- <None or explicit item>

This packet is `report_only`. It does not accept completion or authorize merge.
