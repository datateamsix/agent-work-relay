@input
---
awr:
  schema: awr.input/v1
  intent: refinement.plan
  parent_work_order_id: <work-order-id>
  correlation_id: <original-correlation-id>
  idempotency_key: <stable-key>
  repository:
    url: <https-repository-url>
    base_ref: <bound-base-ref>
  requested_executor: <existing-executor>
  requested_authority: plan_only
---

# Refinement: <short title>

## What changed

<New requirement, corrected assumption, or narrowed scope.>

## What remains unchanged

- <Preserved requirement or boundary>

## Updated acceptance criteria

- [ ] <Observable pass condition>

## Response requested

Return a new immutable plan version and identify every material difference from
the previous plan. Do not modify the repository.
