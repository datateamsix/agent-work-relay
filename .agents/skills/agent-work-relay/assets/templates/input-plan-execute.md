@input
---
awr:
  schema: awr.input/v1
  intent: plan.execute
  parent_work_order_id: <work-order-id>
  correlation_id: <correlation-id>
  idempotency_key: awr:<work-order-id>:plan.execute:v1
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

Required protocol: `schema`, `intent`, `idempotency_key`.
Required lifecycle bindings: parent work order, exact approved plan ID and
SHA-256, and the stored approval receipt. Optional provider: executor hint.

## Approved scope

<Concise scope copied from the stored approval. Do not widen it.>

## Required evidence

- Changes mapped to acceptance criteria
- Commands and tests with results
- Exact before and after commits
- Migrations, deviations, and residual risk as references

This input does not itself grant authority. The broker must already have
`approve_plan` for this fingerprint. A request to execute does not authorize
merge, main-branch push, deployment, or completion acceptance.

If EX-01 orchestration tools are not listed, stop and say so.
