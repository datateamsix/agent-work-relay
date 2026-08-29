@response
---
awr:
  schema: awr.response/v1
  response_type: execution.acknowledged
  work_order_id: <work-order-id>
  in_reply_to: <execution-input-message-id>
  executor_run_id: <provider-run-id>
  source_input_sha256: sha256:<digest>
  idempotency_key: <stable-key>
---

# Execution accepted

- Executor: <provider and agent>
- Repository: <repository>
- Base ref and commit: <ref and SHA>
- Approved plan: <plan ID and SHA-256>
- Approval receipt: <receipt ID>
- Effective authority: <bounded scope>
- Started at: <timestamp>
