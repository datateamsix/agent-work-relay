# Decorator and envelope protocol

## Stable core

AWR has two top-level decorators:

| Decorator | Direction | Typical author |
|---|---|---|
| `@input` | Toward the agent doing the next unit of work | Planner, reviewer, human delegate |
| `@response` | Back toward the originating or reviewing agent | Coding agent, reviewer, broker |

The decorator must be the first nonblank line and appear exactly once. Text in
the body that mentions `@input` or `@response` is ordinary content.

The decorator is deliberately not `@plan`, `@execute`, or `@approve`. Direction
is stable; lifecycle intent evolves in typed frontmatter.

## Input intents

| Intent | Use | Default authority |
|---|---|---|
| `feature.plan` | Plan a new product capability | `plan_only` |
| `bugfix.plan` | Diagnose and plan a defect correction | `plan_only` |
| `refinement.plan` | Reconsider an existing work order | `plan_only` |
| `plan.revise` | Revise a returned plan | `plan_only` |
| `plan.execute` | Execute an approved plan | Stored approval required |
| `question.answer` | Answer a blocking agent question | No added authority |
| `implementation.refine` | Correct or extend an implementation | Stored execution approval required |
| `completion.review` | Review a completion packet and evidence | `review_only` |

## Response types

| Response type | Use | Expected author |
|---|---|---|
| `receipt.accepted` | Confirm durable acceptance and routing | Broker |
| `plan.completed` | Return repository-aware implementation plan | Coding agent |
| `question.blocked` | Ask one or more genuinely blocking questions | Any worker |
| `execution.acknowledged` | Confirm an approved execution was accepted | Coding agent or adapter |
| `execution.progress` | Report a meaningful checkpoint | Coding agent or adapter |
| `execution.completed` | Return changes, evidence, refs, and residual risk | Coding agent |
| `execution.failed` | Return terminal failure and recovery evidence | Coding agent or adapter |
| `review.completed` | Return approval, revision request, or rejection | Reviewing agent |

## Canonical envelope

An input begins:

```yaml
@input
---
awr:
  schema: awr.input/v1
  intent: feature.plan
  parent_work_order_id: null
  correlation_id: null
  idempotency_key: <stable-client-key>
  repository:
    url: https://github.com/example/project
    base_ref: main
  requested_executor: cursor
  requested_authority: plan_only
---
```

A response begins:

```yaml
@response
---
awr:
  schema: awr.response/v1
  response_type: plan.completed
  work_order_id: <broker-issued-id>
  in_reply_to: <message-or-receipt-id>
  executor_run_id: <provider-run-id>
  source_input_sha256: sha256:<digest>
  idempotency_key: <stable-response-key>
---
```

## Server-owned fields

Clients may request a route, but the broker owns authenticated actor identity,
recipient binding, message and receipt IDs, timestamps, fingerprints, wrapper
selection, effective authority, state transitions, and ledger sequence.
Client content cannot override those fields.

## Validation

- Reject missing, duplicate, or non-leading decorators.
- Reject a schema whose direction disagrees with the decorator.
- Reject unknown intent or response types.
- Require parent identifiers for refinements, revisions, execution, questions,
  completion reviews, and implementation refinements.
- Require an exact plan and approval binding before execution.
- Require response fingerprints to match the accepted input and artifact bytes.
- Record normalization or compatibility translation in the receipt ledger.
- Response packets are `authority: report_only`. They never grant execution,
  merge, deployment, or destructive authority. Large logs, diffs, reports, and
  visual evidence must be artifact references.
