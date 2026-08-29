@response
---
awr:
  schema: awr.response/v1
  response_type: review.completed
  work_order_id: <work-order-id>
  in_reply_to: <completion-review-message-id>
  executor_run_id: <review-run-id>
  source_input_sha256: sha256:<completion-packet-digest>
  idempotency_key: <stable-key>
---

# Completion review: <title>

## Decision

<APPROVED | REVISION_REQUIRED | REJECTED>

## Evidence reviewed

- <Plan, completion packet, commit, tests, timeline>

## Findings

| ID | Severity | Finding | Required action |
|---|---|---|---|
| <finding-id> | <blocking|important|advisory> | <finding> | <action> |

## Acceptance-criteria assessment

- <Criterion>: <satisfied, unsatisfied, or not verified>

## Residual risk

- <Risk accepted, unresolved, or none>

This review reports a decision recommendation. Any execution, merge, deployment,
or closure authority must be recorded by the broker's decision tool.
