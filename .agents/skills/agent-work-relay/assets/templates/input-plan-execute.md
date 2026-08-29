@input
---
awr:
  schema: awr.input/v1
  intent: plan.execute
  parent_work_order_id: <work-order-id>
  correlation_id: <correlation-id>
  idempotency_key: <stable-key>
  plan_id: <approved-plan-id>
  plan_sha256: sha256:<digest>
  approval_receipt_id: <broker-issued-approval-id>
  repository:
    url: <https-repository-url>
    base_ref: <approved-base-ref-or-commit>
  requested_executor: <cursor|claude-code|gemini|other>
  requested_authority: approved_execution
---

# Execute approved plan: <title>

## Approved scope

<Concise scope bound by the approval receipt.>

## Required evidence

- Changes mapped to acceptance criteria
- Commands and tests with results
- Exact before/after commit and branch or pull-request references
- Migrations, deployment changes, deviations, and residual risk

Do not exceed the stored approval. Raise a blocking question for a material
scope or authority ambiguity.
