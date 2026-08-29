@response
---
awr:
  schema: awr.response/v1
  response_type: question.blocked
  work_order_id: <work-order-id>
  in_reply_to: <message-id>
  executor_run_id: <provider-run-id>
  source_input_sha256: sha256:<digest>
  idempotency_key: <stable-key>
---

# Blocking questions

## <question-id>: <short question>

**Why this blocks:** <Decision or risk that cannot be resolved safely.>

**Options considered:**

1. <Option and effect>
2. <Option and effect>

**Recommended default:** <Recommendation, if one is safe>

**Work completed before blocking:** <None or safe read-only progress>
