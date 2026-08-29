@input
---
awr:
  schema: awr.input/v1
  intent: plan.revise
  parent_work_order_id: <work-order-id>
  correlation_id: <correlation-id>
  idempotency_key: awr:<work-order-id>:plan.revise:v1
  plan_id: <returned-plan-id>
  plan_sha256: sha256:<digest>
  requested_authority: plan_only
---

# Revise plan: <title>

Required protocol: `schema`, `intent`, `idempotency_key`.
Required lifecycle bindings: parent work order, exact `plan_id` and
`plan_sha256`. Recommended narrative: bounded revision request.

## Revision requested

<Specific change to the returned plan. Do not restated the entire plan.>

## Still in scope

- <Unchanged requirement>

## Out of scope

- <Must not expand>

A plan revision is still plan-only. It does not authorize execution.
