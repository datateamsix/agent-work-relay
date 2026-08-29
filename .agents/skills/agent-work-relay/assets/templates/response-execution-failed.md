@response
---
awr:
  schema: awr.response/v1
  response_type: execution.failed
  work_order_id: <work-order-id>
  in_reply_to: <ack-or-progress-id>
  idempotency_key: awr:<work-order-id>:execution.failed:v1
  source_input_sha256: sha256:<digest>
  created_at: <rfc3339-timestamp>
  authority: report_only
  executor_run_id: <bound-provider-run-id>
  content_sha256: sha256:<canonical-packet-digest>
  error_type: <classification>
---

# Execution failed: <stage>

Never report a partial run as completed.

## Failure

- Classification: <error type>
- Stage: <acknowledge|implement|verify|deliver>
- <Concise description without secret values.>

## Last known safe state

- Base commit: <SHA>
- Last successful checkpoint: <SHA or none>

## Partial mutations and cleanup

- <What changed and whether it was committed, pushed, deployed, or cleaned up>

## Evidence

- <Sanitized log or failed-check artifact ID>

## Recovery

- Retryable: <yes|no|unknown>
- Recommended next step: <bounded recommendation>

This packet is `report_only`.
