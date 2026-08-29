# AWR-LC-01B response integration

LC-01B ports a minimized lifecycle onto the AWR-AS-03 response contract.
There is one response family: `awr.response/v1` in `src/awr/responses/`.
`src/awr/messages/**` is not part of this branch.

LC-01A commits `c291914` and `224c3e2` remain on
`feature/awr-lc-01-response-lifecycle` as reference only. They were not
merged or cherry-picked wholesale.

## Authority

`request_plan_approval` is a broker event (`plan.approval_requested`), not a
stored decision. Only authenticated `approve_plan`, `reject_plan`,
`accept_completion`, `request_revision`, and `cancel` are `StoredDecision`
rows. Responses always carry `authority: report_only`.

## Execution binding

`plan.execute` moves `READY_FOR_EXECUTION` to `EXECUTION_DISPATCHED`.
`EXECUTING` is established only by a valid `execution.acknowledged` packet
whose `executor_run_id` becomes the bound provider run. Later progress,
completion, and failure packets must repeat that run ID.

## Allowed edges

Cancel is one family rule over every non-terminal operational state. It is
not one table row per state.

| Initiating input | Prior state | Identity | Lineage | Fingerprint | Ledger event | Resulting state |
|---|---|---|---|---|---|---|
| `plan.completed` response | `PLANNING` | Participant | `in_reply_to` = current parent; `source_input_sha256` = accepted input | Packet SHA-256 becomes `plan_sha256` | `plan.completed` | `PLAN_READY` |
| `question.blocked` response | `PLANNING` | Participant | Same parent and source rules | Packet SHA-256 | `question.blocked` | `WAITING_FOR_INPUT` (`blocked_from=PLANNING`) |
| Broker `plan.approval_requested` | `PLAN_READY` | Participant | Parent advances to the broker message | None | `plan.approval_requested` | `WAITING_FOR_PLAN_APPROVAL` |
| Decision `approve_plan` | `WAITING_FOR_PLAN_APPROVAL` | Participant | Work-order ID | Exact stored `plan_id` + `plan_sha256` | `decision.approve_plan` | `READY_FOR_EXECUTION` |
| Decision `reject_plan` | `WAITING_FOR_PLAN_APPROVAL` | Participant | Work-order ID | Targets the stored plan | `decision.reject_plan` | `PLAN_READY` |
| Broker `plan.execute` | `READY_FOR_EXECUTION` | Participant | Parent advances to the dispatch id | Stored plan approval for that fingerprint | `plan.execute` | `EXECUTION_DISPATCHED` |
| `execution.acknowledged` response | `EXECUTION_DISPATCHED` | Participant | Parent and source; stored plan approval | Packet SHA-256; binds `executor_run_id` | `execution.acknowledged` | `EXECUTING` |
| `execution.progress` response | `EXECUTING` | Participant | Parent, source, bound run | Packet SHA-256 | `execution.progress` | `EXECUTING` |
| `execution.completed` response | `EXECUTING` | Participant | Parent, source, bound run | Packet SHA-256 | `execution.completed` | `COMPLETION_READY` |
| `execution.failed` response | `EXECUTING` | Participant | Parent, source, bound run | Packet SHA-256 | `execution.failed` | `FAILED` |
| `question.blocked` response | `EXECUTING` | Participant | Parent, source, bound run | Packet SHA-256 | `question.blocked` | `WAITING_FOR_INPUT` (`blocked_from=EXECUTING`) |
| Broker `completion.review` | `COMPLETION_READY` | Participant | Parent advances | None | `completion.review` | `PLANNER_REVIEWING` |
| `review.completed` APPROVED or REJECTED | `PLANNER_REVIEWING` | Participant | Parent and source | Packet SHA-256 | `review.completed` | `WAITING_FOR_HUMAN_REVIEW` |
| `review.completed` REVISE | `PLANNER_REVIEWING` | Participant | Parent and source | Packet SHA-256 | `review.completed` | `REVISION_REQUIRED` |
| Decision `accept_completion` | `WAITING_FOR_HUMAN_REVIEW` | Participant | Work-order ID; not while a question is blocking | Completion target | `decision.accept_completion` | `COMPLETE` |
| Decision `request_revision` | `WAITING_FOR_HUMAN_REVIEW` | Participant | Same; not while a question is blocking | Completion or review target | `decision.request_revision` | `REVISION_REQUIRED` |
| Broker `question.answer` | `WAITING_FOR_INPUT` | Participant | Requires `blocked_from` | None | `question.answer` | Restored `blocked_from` state |
| Broker `implementation.refine` | `REVISION_REQUIRED` | Participant | Original lineage preserved | Stored plan approval | `implementation.refine` | `EXECUTION_DISPATCHED` |
| Decision `cancel` | Any cancellable state | Participant | Work-order ID | Work-order or plan target | `decision.cancel` | `CANCELLED` |

Cancellable states: `PLANNING`, `PLAN_READY`, `WAITING_FOR_PLAN_APPROVAL`,
`READY_FOR_EXECUTION`, `EXECUTION_DISPATCHED`, `EXECUTING`,
`COMPLETION_READY`, `PLANNER_REVIEWING`, `REVISION_REQUIRED`,
`WAITING_FOR_HUMAN_REVIEW`, `WAITING_FOR_INPUT`.

`WAITING_FOR_HUMAN_REVIEW` remains the post-review human gate
(`accept_completion` / `request_revision`). Blocking questions use the
distinct `WAITING_FOR_INPUT` state.

## Decisions

`record_decision` requires a compact rationale (512 bytes). `expires_at` is
optional. Idempotent replay compares the complete canonical decision
fingerprint (type, work order, actor, target, action, scope, key, rationale,
and expiry) and returns the original stored receipt object.

Only decision principals (the work-order sender) may record human decisions.
Recipient, bound-agent, and other executor identities are rejected.

## Reads

`get_work_order` and `list_pending_actions` authorize the explicit actor when
one is supplied, otherwise the authenticated principal, as a work-order
participant. HTTP GET `/v1/work-orders/{id}` and `/pending` accept `sender`.
`list_pending_actions` without `work_order_id` scans work orders the actor may
read; calling it with neither an actor nor a work order ID is an error, not an
empty list. `get_plan`, timeline, and artifact projections apply the same
participant check when an explicit actor is supplied.

Stored snapshots that predate principal fields hydrate `decision_principals`
from the work-order sender and `executor_principals` from the recipient. The
kernel fails closed if those sets are empty.

Removed convenience edges include `PLAN_READY` → approve or execute,
`READY_FOR_EXECUTION` → `EXECUTING`, `plan.execute` → `EXECUTING`,
`review.completed` → `COMPLETE`, and per-state cancel rows.

## Durable projection

`WorkStatus` is the operational projection. SQLite, Firestore, and the
Firestore test double persist status, response packets, decisions, and the
lifecycle snapshot in the same work-order transaction as the ledger append.

## Tools

| Tool | Scope |
|---|---|
| `submit_response` | `awr:response` |
| `record_decision` | `awr:decide` |
| `list_pending_actions` | `awr:read` |
| `get_work_order` | `awr:read` |

Planning, work-bundle, artifact, HTTP-cache, gzip, and idempotency behavior
from AS-03 is unchanged.
