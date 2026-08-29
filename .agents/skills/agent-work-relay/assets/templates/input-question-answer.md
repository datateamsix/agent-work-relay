@input
---
awr:
  schema: awr.input/v1
  intent: question.answer
  parent_work_order_id: <work-order-id>
  correlation_id: <correlation-id>
  idempotency_key: awr:<work-order-id>:question.answer:v1
  question_ids:
    - <question-id>
  requested_authority: no_change
---

# Answers to blocking questions

Required protocol: `schema`, `intent`, `idempotency_key`.
Required lifecycle bindings: parent work order and cited question IDs.

## <question-id>

**Answer:** <Direct answer>

**Effect on scope:** <none or explicit change>

**Effect on acceptance criteria:** <none or explicit change>

**Effect on authority:** None. Execution still requires a stored decision.
