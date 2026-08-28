# EWB-GT-001 — ChatGPT-to-Cursor prompt-to-plan

## Goal

Prove that a planning agent can hand a Markdown feature or refinement prompt to
a Cursor executor without copy/paste, and that every boundary produces an
auditable receipt.

## Input

```markdown
@ewb feature.plan

# Add project health endpoint

Produce an implementation plan. Do not edit files.
```

## Expected ledger

| Sequence | Event | Actor | Counterparty |
|---:|---|---|---|
| 1 | `work_order.accepted` | planner | broker |
| 2 | `work_order.routed` | broker | cursor executor |
| 3 | `executor.acknowledged` | cursor executor | broker |

## Assertions

- The content SHA-256 in the receipt matches the submitted bytes.
- The applied wrapper is `feature.plan@1.0.0`.
- The executor receives `mode=PLAN_ONLY`.
- The executor call count is one.
- Replaying the idempotency key returns the same work-order ID.
- The replay does not launch a second executor run.
- No repository mutation or pull request is permitted in this phase.

## Next extension

Add a `plan.ready` packet, route it back to the planner, and support a
`@ewb refinement.plan parent=<id>` response in the same conversation thread.
