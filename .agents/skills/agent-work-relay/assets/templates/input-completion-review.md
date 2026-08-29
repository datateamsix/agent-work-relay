@input
---
awr:
  schema: awr.input/v1
  intent: completion.review
  parent_work_order_id: <work-order-id>
  correlation_id: <correlation-id>
  idempotency_key: <stable-key>
  completion_packet_id: <completion-packet-id>
  completion_sha256: sha256:<digest>
  requested_reviewer: <planner-or-review-agent>
  requested_authority: review_only
---

# Review implementation completion

Review the approved plan, completion packet, repository evidence, and timeline.

## Review criteria

- Approved scope matches actual changes
- Acceptance criteria are individually evidenced
- Tests and verification are reproducible
- Security, data, migration, deployment, and rollback risks are addressed
- No unauthorized external mutation occurred
- Deviations and residual risks are explicit

Return `APPROVED`, `REVISION_REQUIRED`, or `REJECTED` with bounded findings.
