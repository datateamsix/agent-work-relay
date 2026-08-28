# AWR-GT-001 — ChatGPT-to-Cursor prompt-to-plan

## Goal

Prove that a planning agent can hand a Markdown feature or refinement prompt to
a Cursor executor without copy/paste, and that every boundary produces an
auditable receipt.

## Input

```markdown
@awr feature.plan

# Add project health endpoint

Produce an implementation plan. Do not edit files.
```

## Expected completed ledger

| Sequence | Event | Actor | Counterparty |
|---:|---|---|---|
| 1 | `work_order.accepted` | planner | broker |
| 2 | `work_order.routed` | broker | cursor executor |
| 3 | `executor.acknowledged` | cursor executor | broker |
| 4 | `plan.received` | cursor executor | broker |
| 5 | `plan.available` | broker | planner |

## Assertions

- The content SHA-256 in the receipt matches the submitted bytes.
- The applied wrapper is `feature.plan@1.0.0`.
- The executor receives `mode=PLAN_ONLY`.
- Cursor receives native `mode=plan`, `workOnCurrentBranch=false`, and
  `autoCreatePR=false`.
- The executor call count is one.
- The terminal plan result is stored with a SHA-256 fingerprint.
- The planner can retrieve the same immutable `PlanPacket` through MCP.
- Replaying the idempotency key returns the same work-order ID.
- The replay does not launch a second executor run.
- Repeated refresh does not duplicate plan ledger entries.
- No repository mutation or pull request is permitted in this phase.

## Live pass criteria

The credential-free recording adapter proves the broker contracts. The live
test passes only when the same flow creates a real Cursor Cloud agent against
the selected repository and returns its final Plan-mode result to the planner.
