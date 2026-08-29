# Human and policy decisions

Decorators and agent prose never grant authority. Stored `record_decision`
rows do.

## Who may decide

Only a decision principal, typically the work-order sender, may record
human decisions. Recipients, bound agents, and other executor identities
cannot.

## Operational stored decisions

| Type | When | Target |
|---|---|---|
| `approve_plan` | `WAITING_FOR_PLAN_APPROVAL` | exact `plan_id` and `plan_sha256` |
| `reject_plan` | `WAITING_FOR_PLAN_APPROVAL` | same plan fingerprint |
| `accept_completion` | `WAITING_FOR_HUMAN_REVIEW` and no blocking question | completion packet |
| `request_revision` | `WAITING_FOR_HUMAN_REVIEW` | completion or review |
| `cancel` | any cancellable state | work order or plan |

Required: compact rationale (512 bytes). Optional: `expires_at`.

`plan.approval_requested` is a broker event, not a stored decision.

## Separate restricted decisions

These require their own later decision types and must not be inferred from
a plan approval or review recommendation:

- merge;
- main-branch push;
- deployment;
- destructive operations.

## Recording

Call `record_decision` only after the tool is listed and the actor is the
principal. Bind the exact target ID and SHA-256. Use a stable idempotency
key. Show the decision receipt.

A review `APPROVED` packet is not acceptance.
