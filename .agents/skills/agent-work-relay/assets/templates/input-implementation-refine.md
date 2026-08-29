@input
---
awr:
  schema: awr.input/v1
  intent: implementation.refine
  parent_work_order_id: <work-order-id>
  correlation_id: <correlation-id>
  idempotency_key: awr:<work-order-id>:implementation.refine:v1
  plan_id: <approved-plan-id>
  plan_sha256: sha256:<digest>
  requested_authority: approved_execution
---

# Implementation refine: <title>

Required protocol: `schema`, `intent`, `idempotency_key`.
Required lifecycle bindings: original work-order lineage, approved plan ID
and SHA-256. Recommended narrative: bounded follow-up only.

## Follow-up requested

<What must change. Preserve the original lineage.>

## Still approved

- <Unchanged plan scope>

Do not start a new work order. Do not treat this as a new plan approval.
