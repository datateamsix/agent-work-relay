# AWR-EX-01 — Durable Cursor execution orchestration

This slice turns an approved plan into a crash-safe Cursor implementation run
without granting merge, main-push, deploy, or artifact-delivery authority.

## Safe dispatch sequence

1. Validate a stored `approve_plan` bound to the exact plan ID and SHA-256.
2. Persist a durable execution-dispatch intent and the `plan.execute` ledger
   event in the same work-order transaction.
3. Commit and release the work-order write lock.
4. Claim a short compare-and-swap lease on the dispatch record.
5. Call the provider outside any database write lock.
6. Persist provider IDs as `PROVIDER_ACCEPTED`, then persist
   `execution.acknowledged` with the lifecycle update and receipt.
7. Enter `EXECUTING` only after that acknowledgement is durable.

`record_decision(approve_plan)` creates eligibility only. It never performs
Cursor I/O. `refresh_external_run` (`awr:execute`) is the reusable reconciler.

## Cursor Cloud Agents capabilities actually verified

Official HTTP behavior used by this slice:

- Create agent: `POST /v1/agents` with `mode: "agent"` (planning remains
  `mode: "plan"`).
- Follow-up: `POST /v1/agents/{id}/runs` can override mode. AWR reuses the
  durable planning agent when `executor.acknowledged` recorded an agent ID.
- Repository and starting ref: `repos: [{url, startingRef}]`.
- Safety controls: `workOnCurrentBranch: false`, `autoCreatePR: false`.
- Client-supplied `agentId` (`bc-…`) is idempotent on create (`409`
  `agent_id_conflict` then `GET /v1/agents/{id}` + `latestRunId`).
- Follow-up runs do not accept a client-supplied run ID. Official HTTP docs
  do not prove exact-once follow-up after a timeout. AWR therefore fails
  closed with `RECONCILIATION_REQUIRED` instead of posting a second run.
- The adapter sends `Idempotency-Key` from the durable provider key. This is
  best-effort; create recovery still relies on the deterministic `agentId`.
- Run status: `GET /v1/agents/{id}/runs/{runId}`.
- Git facts are recorded only when the adapter returns structured fields.
  A textual claim is not proof of merge, deploy, or a main-branch update.

## Fail-closed limitations

- Follow-up reuse cannot prove exact-once acceptance after timeout or
  `agent_busy`. The dispatch is marked `RECONCILIATION_REQUIRED`.
- Unstructured Cursor terminal output is never inferred as success. AWR
  records `execution.failed` with `MALFORMED_EXECUTOR_RESPONSE`.
- Execution that depends on undelivered artifacts returns
  `DELIVERY_UNSUPPORTED`. Artifact bodies remain AWR-AS-04.
- Completion stays in `COMPLETION_READY` until planner review and human
  `accept_completion`.
