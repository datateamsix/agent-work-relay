@input
---
awr:
  schema: awr.input/v1
  intent: plan.revise
  parent_work_order_id: <work-order-id>
  correlation_id: <correlation-id>
  idempotency_key: <stable-key>
  plan_id: <plan-id>
  plan_sha256: sha256:<digest>
  requested_executor: <same-planning-agent>
  requested_authority: plan_only
---

# Plan revision request

## Review findings

- <Specific issue in the existing plan>

## Required changes

- <Required revision>

## Preserve

- <Approved or correct part of the plan>

## Response requested

Return a complete replacement plan as a new version, plus a concise change log.
