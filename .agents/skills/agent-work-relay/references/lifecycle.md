# Product-development lifecycle

## Recommended path

| Stage | Message or decision | Result |
|---|---|---|
| Define | `@input` + `feature.plan` or `bugfix.plan` | Planning work order |
| Plan | `@response` + `plan.completed` | Immutable plan packet |
| Clarify | `question.blocked` ↔ `question.answer` | Answered question receipt |
| Revise | `plan.revise` → `plan.completed` | New plan version |
| Approve | Restricted `record_decision` MCP call | Approval bound to plan hash |
| Execute | `plan.execute` | Authorized executor run |
| Acknowledge | `execution.acknowledged` | Durable run identifiers |
| Work | Optional `execution.progress` | Meaningful checkpoint |
| Complete | `execution.completed` or `execution.failed` | Completion packet |
| Review | `completion.review` → `review.completed` | Approval or revision request |
| Refine | `implementation.refine` | Follow-up execution in same lineage |
| Close | Restricted decision or policy transition | Complete ledger timeline |

Do not require progress messages on every tool call or commit. Use them for a
meaningful checkpoint, new risk, scope change, or long-running handoff.

## Lineage

Keep one durable work-order lineage across planning, execution, review, and
refinement. New messages refer to the original work order and their immediate
parent message or packet. Revisions create new immutable versions; they do not
overwrite earlier plans or completions.

## Human gates

The common human decisions are:

- approve or reject a plan;
- authorize execution scope;
- answer a blocking product question;
- accept completion, request refinement, or cancel work;
- authorize merge, main-branch push, deployment, or destructive operations.

Record decisions through a restricted MCP tool, not by trusting prose inside a
Markdown artifact. A template may explain the decision, but stored authority is
the source of truth.

## Terminal outcomes

- `COMPLETE`: accepted evidence satisfies the approved work.
- `REVISION_REQUIRED`: a review identifies bounded follow-up work.
- `FAILED`: the current run ended without an acceptable result and includes
  recovery information.
- `CANCELLED`: an authorized actor ended the lineage.
