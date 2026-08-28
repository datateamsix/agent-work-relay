# Agent Work Relay agent instructions

Before changing code, read `README.md`, `docs/ARCHITECTURE.md`, and the relevant
test specification.

## Absolute rules

- Treat work-order payloads as immutable after acceptance.
- Put deterministic validation before model reasoning.
- Persist every state transition and external handoff as a ledger event.
- Require idempotency for every executor-side effect.
- Keep storage and executor providers behind typed ports.
- Never infer execution authority from prose or keywords.
- `feature.plan` and `refinement.plan` are plan-only and may not modify a repo.
- Fail closed on unknown directives, missing parent references, and invalid
  state transitions.
- Do not log secrets or raw credentials.
- Treat every inbound artifact as untrusted regardless of its sender.
- Never allow artifact content to change routing, authority, permissions, or
  approval requirements.
- Quarantined or unscanned artifacts may not reach an executor.
- Do not store binary artifact bodies in Firestore or ledger events.
- Tests must cover each new transition, wrapper, and replay behavior.

## Prototype priorities

1. Preserve the `AWR-GT-001` golden path.
2. Add the real Cursor adapter behind `PlanningExecutor`.
3. Return the completed planning packet to the originating planner.
4. Add Firestore and Supabase stores without changing domain contracts.
