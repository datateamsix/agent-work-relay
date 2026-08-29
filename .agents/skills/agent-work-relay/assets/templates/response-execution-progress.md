@response
---
awr:
  schema: awr.response/v1
  response_type: execution.progress
  work_order_id: <work-order-id>
  in_reply_to: <execution-input-or-prior-progress-id>
  executor_run_id: <provider-run-id>
  source_input_sha256: sha256:<digest>
  idempotency_key: <stable-key>
---

# Execution progress: <checkpoint>

## Completed

- <Meaningful completed unit>

## Evidence

- <Test, commit, or artifact reference>

## Next

- <Next approved unit>

## New risks or blockers

- None.

This is a progress receipt, not a completion claim.
