@response
---
awr:
  schema: awr.response/v1
  response_type: review.completed
  work_order_id: <work-order-id>
  in_reply_to: <completion-review-id>
  idempotency_key: awr:<work-order-id>:review.completed:v1
  source_input_sha256: sha256:<completion-or-input-digest>
  created_at: <rfc3339-timestamp>
  authority: report_only
  content_sha256: sha256:<canonical-packet-digest>
  outcome: <APPROVED|REVISE|REJECTED>
---

# Completion review: <title>

Required protocol fields are in the envelope. Packet outcome must be
`APPROVED`, `REVISE`, or `REJECTED`. Only `REVISE` yields
`REVISION_REQUIRED`.

## Decision

<APPROVED | REVISE | REJECTED>

## Evidence reviewed

- <Plan, completion packet, commits, tests, timeline>

## Findings

| ID | Severity | Finding | Required action |
|---|---|---|---|
| <finding-id> | <blocking|important|advisory> | <finding> | <action> |

## Acceptance-criteria assessment

- <Criterion>: <satisfied, unsatisfied, or not verified>

## Residual risk

- <Risk accepted, unresolved, or none>

This review is `report_only`. It grants no close, merge, or deploy authority.
Only a stored `accept_completion` decision closes the work order.
