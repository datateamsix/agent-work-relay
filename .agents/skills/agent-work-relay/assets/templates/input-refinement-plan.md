@input
---
awr:
  schema: awr.input/v1
  intent: refinement.plan
  parent_work_order_id: <original-work-order-id>
  correlation_id: <correlation-id>
  idempotency_key: awr:<work-order-id>:refinement.plan:v1
  repository:
    url: <https-repository-url>
    base_ref: main
  requested_executor: <cursor|claude-code|gemini|other>
  requested_authority: plan_only
---

# Refine request: <title>

Required protocol: `schema`, `intent`, `idempotency_key`.
Required lifecycle bindings: `parent_work_order_id` and the original source
fingerprint from the receipt. Recommended narrative: what changed and why.

## Why this refinement

<Product or discovery change. Preserve the original lineage.>

## Changed requirements

- <Delta only>

## Unchanged constraints

- <Still true>

## Acceptance criteria

- [ ] <Updated or confirmed observable condition>

## Planning response requested

Return a new immutable plan version. Do not overwrite the prior plan.
Do not execute.
