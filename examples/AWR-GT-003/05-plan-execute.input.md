@input
---
awr:
  schema: awr.input/v1
  intent: plan.execute
  parent_work_order_id: wo_gt003_feature_catalog
  correlation_id: corr_gt003_plan_execute
  idempotency_key: awr:wo_gt003_feature_catalog:plan.execute:plan_gt003_v1:v1
  plan_id: plan_gt003_v1
  plan_sha256: sha256:b57127b6d04ea32e51637c58592b34c1bcc2ef314ada155fe764aaca0ed17f13
  approval_receipt_id: dec_gt003_approve_plan
  repository:
    url: https://github.com/example/fixture-relay
    base_ref: main
    base_sha: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  requested_executor: cursor
  requested_authority: approved_execution
---

# Execute approved plan: Catalog search

Required lifecycle bindings: parent work order, exact approved plan ID and
SHA-256, and the stored approval receipt.

## Approved scope

Add catalog search with pagination for published products only.

## Required evidence

- Changes mapped to acceptance criteria
- Commands and tests with results
- Exact before and after commits

This input does not itself grant authority. A request to execute does not
authorize merge, main-branch push, deployment, or completion acceptance.

If EX-01 orchestration tools are not listed, stop and say so.
