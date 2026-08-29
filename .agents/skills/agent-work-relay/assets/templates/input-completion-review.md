@input
---
awr:
  schema: awr.input/v1
  intent: completion.review
  parent_work_order_id: <work-order-id>
  correlation_id: <correlation-id>
  idempotency_key: awr:<work-order-id>:completion.review:v1
  completion_id: <completion-packet-id>
  completion_sha256: sha256:<digest>
  requested_authority: review_only
---

# Completion review: <title>

Required protocol: `schema`, `intent`, `idempotency_key`.
Required lifecycle bindings: parent work order and completion fingerprint.

## Review the

- approved plan fingerprint
- completion packet
- timeline and receipts
- cited tests and artifact references

Return `review.completed` with `APPROVED`, `REVISE`, or `REJECTED`.
This request asks for a recommendation. It does not close the work order.
