# Decorator and envelope protocol

## Stable core

AWR has two top-level decorators:

| Decorator | Direction | Typical author |
|---|---|---|
| `@input` | Toward the agent doing the next unit of work | Planner, reviewer, human delegate |
| `@response` | Back toward the originating or reviewing agent | Worker, reviewer, broker, adapter |

The decorator must be the first nonblank line and appear exactly once. A body
mention of `@input` or `@response` is ordinary text.

Do not use `@plan`, `@execute`, or `@approve`. Direction is stable. Lifecycle
intent lives in typed frontmatter.

## Input intents

| Intent | Use | Default requested authority |
|---|---|---|
| `feature.plan` | Plan a new product capability | `plan_only` |
| `bugfix.plan` | Diagnose and plan a defect correction | `plan_only` |
| `refinement.plan` | Reconsider an existing work order | `plan_only` |
| `plan.revise` | Ask for a bounded revision of a returned plan | `plan_only` |
| `plan.execute` | Ask the broker to dispatch approved execution | Stored approval required |
| `question.answer` | Answer a blocking question | No added authority |
| `implementation.refine` | Bounded follow-up in the same lineage | Stored plan approval required |
| `completion.review` | Ask a reviewer to recommend | `review_only` |

On this baseline, `feature.plan` and `refinement.plan` transmit through
`submit_prompt_for_planning`. Other `@input` intents are adapter or broker
events until a `submit_input` tool exists. See
[capability.md](capability.md).

## Response types

| Response type | Use | Author |
|---|---|---|
| `receipt.accepted` | Durable acceptance and routing | Broker |
| `plan.completed` | Repository-aware implementation plan | Worker |
| `question.blocked` | Genuinely blocking questions | Worker |
| `execution.acknowledged` | Bind the provider run | Worker or adapter |
| `execution.progress` | Meaningful checkpoint | Worker or adapter |
| `execution.completed` | Terminal success evidence | Worker or adapter |
| `execution.failed` | Terminal failure and recovery | Worker or adapter |
| `review.completed` | `APPROVED`, `REVISE`, or `REJECTED` | Reviewer |

Review packet outcome `REVISE` yields work-order state `REVISION_REQUIRED`.
The packet does not close the work order.

## Canonical envelopes

Required protocol fields are in the `awr` mapping. Recommended narrative
sections are in the body. Optional provider fields are normalized by the
adapter. See each template.

Responses must include `schema: awr.response/v1`, `authority: report_only`,
`work_order_id`, `in_reply_to`, `idempotency_key`, `source_input_sha256`,
and `created_at`. Include `executor_run_id` after a run is bound. Include
`content_sha256` when the renderer has computed the canonical digest.

Large logs, complete diffs, reports, and screenshots are artifact
references. They do not belong inline. Forbidden compact-packet keys include
`diff`, `log`, `logs`, `bytes`, `screenshot`, and `signed_url`.

## Validation

- Reject missing, duplicate, or non-leading decorators.
- Reject a schema whose direction disagrees with the decorator.
- Reject unknown intent or response types.
- Never guess missing broker-issued identifiers.
- Response packets never grant execution, merge, deployment, or destructive
  authority.
