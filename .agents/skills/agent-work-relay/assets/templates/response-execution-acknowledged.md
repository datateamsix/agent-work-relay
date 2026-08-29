@response
---
awr:
  schema: awr.response/v1
  response_type: execution.acknowledged
  work_order_id: <work-order-id>
  in_reply_to: <execution-dispatch-id>
  idempotency_key: awr:<work-order-id>:execution.acknowledged:v1
  source_input_sha256: sha256:<digest>
  created_at: <rfc3339-timestamp>
  authority: report_only
  executor_run_id: <provider-run-id>
  content_sha256: sha256:<canonical-packet-digest>
  executor: <cursor:cloud|other>
---

# Execution acknowledged

Required protocol fields are in the envelope. Required lifecycle bindings:
stored plan approval, dispatch parent, and a durable `executor_run_id`.

- Executor: <provider>
- Provider run: <executor_run_id>
- Approved plan: <plan ID and SHA-256>

Later progress, completion, and failure packets must repeat this run ID.
This packet does not mean work has finished.
