@response
---
awr:
  schema: awr.response/v1
  response_type: question.blocked
  work_order_id: <work-order-id>
  in_reply_to: <message-id>
  idempotency_key: awr:<work-order-id>:question.blocked:v1
  source_input_sha256: sha256:<digest>
  created_at: <rfc3339-timestamp>
  authority: report_only
  executor_run_id: <provider-run-id-or-omit>
  content_sha256: sha256:<canonical-packet-digest>
---

# Blocking questions

Required protocol fields are in the envelope. The parser reads the
`questions` collection from `- <id>: <text>` lines. Do not ask preference
questions that existing requirements already answer.

- q1: <direct question>

## q1

**Why this blocks:** <Decision or risk that cannot be resolved safely.>

**Options and effects:**

1. <Option and effect>
2. <Option and effect>

**Safe recommendation:** <Recommendation, or none>

**Last safe state:** <None, or read-only progress / last commit>
