@response
---
awr:
  schema: awr.response/v1
  response_type: execution.failed
  work_order_id: <work-order-id>
  in_reply_to: <execution-input-message-id>
  executor_run_id: <provider-run-id>
  source_input_sha256: sha256:<digest>
  idempotency_key: <stable-key>
---

# Execution failed: <stage>

## Failure

<Concise description without secret values.>

## Last known safe state

- Base commit: <SHA>
- Last successful commit or checkpoint: <SHA or none>
- External mutations: <none or explicit list>

## Partial work

- <What changed and whether it was committed, pushed, deployed, or cleaned up>

## Evidence

- <Error classification, sanitized log reference, or failed check>

## Recovery

- Retryable: <yes|no|unknown>
- Cleanup required: <action or none>
- Recommended next step: <bounded recommendation>
