@input
---
awr:
  schema: awr.input/v1
  intent: question.answer
  parent_work_order_id: <work-order-id>
  correlation_id: <correlation-id>
  idempotency_key: <stable-key>
  question_ids:
    - <question-id>
  requested_authority: no_change
---

# Answers to blocking questions

## <question-id>

**Answer:** <Direct answer>

**Effect on scope:** <none or explicit change>

**Effect on acceptance criteria:** <none or explicit change>

**Effect on authority:** None. Any new execution authority requires a separate
broker decision.
