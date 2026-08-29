@input
---
awr:
  schema: awr.input/v1
  intent: implementation.refine
  parent_work_order_id: <work-order-id>
  correlation_id: <correlation-id>
  idempotency_key: <stable-key>
  completion_packet_id: <completion-packet-id>
  review_id: <review-id>
  approval_receipt_id: <refinement-approval-id>
  requested_executor: <existing-executor>
  requested_authority: approved_refinement
---

# Implementation refinement: <title>

## Review findings to address

- <Finding with acceptance criterion or evidence reference>

## Preserve

- <Correct existing behavior that must not regress>

## Completion evidence required

- Focused regression evidence for each finding
- Full relevant suite status
- Exact new commit or pull-request references
- Deviations and residual risk
