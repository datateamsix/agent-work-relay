# Idempotency and lineage

## Key pattern

Use a key that is stable for retrying the same semantic operation and
different when the payload or operation changes:

```text
awr:<work-order-or-client-correlation>:<message-type>:<attempt-or-version>
```

Examples:

```text
awr:gt003:feature.plan:v1
awr:AWR-1001:plan.completed:v1
awr:AWR-1001:approve_plan:v1
awr:AWR-1001:execution.completed:v1
```

Do not use timestamps or random values when deterministic replay is required.

## Do not reuse a key for

- different packet content;
- a revised plan;
- a later execution attempt;
- a different actor;
- a different response type or decision type.

The broker binds an idempotency key to the canonical packet or decision
fingerprint. A replay with the same key and same body returns the original
receipt. A replay with the same key and a different body fails closed.

## Lineage bindings

Every refinement, question, execution result, and review must preserve:

- the original work-order ID;
- the immediate parent message or packet ID;
- the accepted source-input SHA-256;
- the relevant plan, run, completion, or review identifiers.

Read those values from `get_work_order` and `get_work_order_timeline`.
Never invent a work-order ID, plan ID, run ID, or parent ID.

## Source fingerprints

Responses repeat `source_input_sha256` from the accepted input. After
`execution.acknowledged`, later execution packets repeat that run's
`executor_run_id`.
