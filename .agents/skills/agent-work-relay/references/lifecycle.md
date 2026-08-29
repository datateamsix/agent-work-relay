# Product-development lifecycle

LC-01B is operational on the `762cbe4` baseline. The graph below is the
runtime graph, not a convenience sketch.

## Recommended path

| Stage | Message or decision | Result |
|---|---|---|
| Define | `@input` + `feature.plan` or `bugfix.plan` | `PLANNING` |
| Plan | `@response` + `plan.completed` | `PLAN_READY` |
| Clarify | `question.blocked` ↔ `question.answer` | `WAITING_FOR_INPUT`, then restored prior state |
| Approve request | broker `plan.approval_requested` | `WAITING_FOR_PLAN_APPROVAL` |
| Approve | `record_decision approve_plan` on exact plan ID and SHA-256 | `READY_FOR_EXECUTION` |
| Dispatch | broker `plan.execute` | `EXECUTION_DISPATCHED` — not executing yet |
| Acknowledge | `execution.acknowledged` | binds the provider run; `EXECUTING` |
| Work | optional `execution.progress` | stays `EXECUTING` |
| Complete | `execution.completed` or `execution.failed` | `COMPLETION_READY` or `FAILED` |
| Review request | broker `completion.review` | `PLANNER_REVIEWING` |
| Review | `review.completed` | recommendation only: `APPROVED` / `REJECTED` → `WAITING_FOR_HUMAN_REVIEW`; only `REVISE` → `REVISION_REQUIRED` |
| Refine | broker `implementation.refine` | `EXECUTION_DISPATCHED` in the same lineage |
| Close | human `accept_completion` | `COMPLETE` |

`WAITING_FOR_HUMAN_REVIEW` is the post-review human gate.
`WAITING_FOR_INPUT` is only for blocking questions.

## Authority split

- Envelope intent describes lifecycle meaning.
- Agent prose cannot create approvals.
- Responses are always `report_only`.
- Exact plan approval is a stored `record_decision`.
- Review `APPROVED` does not close the work order.
- Coding agents may not approve their own plans or completions.
- Merge, main push, deployment, cancellation, and destructive actions need
  separate restricted decisions.

## Lineage

Keep one work-order lineage. New messages cite the original work order and
the immediate parent message or packet. Revisions create new immutable
versions. They do not overwrite earlier plans or completions.

## Progress discipline

Do not emit `execution.progress` for every edit, command, or commit. Use it
for a milestone, new material risk, scope change, blocker, or long-running
checkpoint.
