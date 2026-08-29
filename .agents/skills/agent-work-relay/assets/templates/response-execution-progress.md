@response
---
awr:
  schema: awr.response/v1
  response_type: execution.progress
  work_order_id: <work-order-id>
  in_reply_to: <ack-or-prior-progress-id>
  idempotency_key: awr:<work-order-id>:execution.progress:v1
  source_input_sha256: sha256:<digest>
  created_at: <rfc3339-timestamp>
  authority: report_only
  executor_run_id: <bound-provider-run-id>
  content_sha256: sha256:<canonical-packet-digest>
  percent: <0-100-or-omit>
---

# Execution progress: <checkpoint>

Use this packet only for a meaningful milestone, new material risk, scope
change, blocker, or long-running checkpoint. Do not emit it for every file
edit, command, or commit.

## Checkpoint

<What became true.>

## Evidence

- <Test, commit, or artifact reference. No complete logs.>

## New risks or blockers

- None.

This is a progress receipt, not a completion claim.
