@response
---
awr:
  schema: awr.response/v1
  response_type: execution.completed
  work_order_id: <work-order-id>
  in_reply_to: <execution-input-message-id>
  executor_run_id: <provider-run-id>
  source_input_sha256: sha256:<digest>
  idempotency_key: <stable-key>
---

# Execution completion: <title>

## Outcome

<What is now true.>

## Changes

- `<path or contract>` — <change and reason>

## Acceptance criteria

| Criterion | Result | Evidence |
|---|---|---|
| <criterion> | PASS / FAIL / NOT VERIFIED | <command, test, URL, or artifact> |

## Verification

| Command or check | Result |
|---|---|
| `<command>` | <result> |

## Git and delivery references

- Repository: <URL>
- Base commit: <SHA>
- Final commit: <SHA>
- Branch: <name or main>
- Pull request: <URL or none>
- Deployment: <reference or not performed>

## Migrations and operations

- <Migration, configuration, secret name, or none; never include secret values>

## Deviations, residual risks, and follow-up

- <None or explicit item>

## Security confirmation

No credentials or secret values are included in this packet.
