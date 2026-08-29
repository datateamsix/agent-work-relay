@response
---
awr:
  schema: awr.response/v1
  response_type: plan.completed
  work_order_id: <work-order-id>
  in_reply_to: <input-message-id>
  idempotency_key: awr:<work-order-id>:plan.completed:v1
  source_input_sha256: sha256:<digest>
  created_at: <rfc3339-timestamp>
  authority: report_only
  executor_run_id: <provider-run-id-or-omit>
  content_sha256: sha256:<canonical-packet-digest>
  plan_id: <plan-id-or-omit>
  body_sha256: sha256:<plan-body-digest>
---

# Implementation plan: <title>

Required protocol fields are in the envelope. Required lifecycle bindings:
parent input, source fingerprint, and a new immutable plan body hash.
Recommended narrative: the compact sections below.

## Understanding and scope

<Repository-grounded interpretation. No exploratory narration.>

## Relevant existing architecture

- <Component, file, or contract. Not a full inventory.>

## Implementation steps

1. <Ordered step with expected files or contracts>

## Test and verification plan

- <Test or proof>

## Risks and assumptions

- <Material risk or assumption>

## Blocking questions

- None, or only questions that block a safe plan.

## Planning-mode confirmation

No files were edited, committed, pushed, deployed, or otherwise mutated.
This packet is `report_only`. It does not approve execution.
