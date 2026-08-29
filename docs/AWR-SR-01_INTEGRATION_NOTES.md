# AWR-SR-01 integration notes

Agent B owns the shared skill bundle. This file records runtime contract
facts discovered against baseline `762cbe4820b2518fc8bbf494367279f1e930459c`.
It does not patch Agent 1 runtime files.

## Codex / OpenAI skill validator

No Codex/OpenAI `skill-creator` or hosted skill linter is installed in this
environment. Validation used the repository scripts:

```bash
uv run python .agents/skills/agent-work-relay/scripts/validate_skill_bundle.py
uv run python .agents/skills/agent-work-relay/scripts/refresh_template_manifest.py
uv run python .agents/skills/agent-work-relay/scripts/validate_gt003.py
```

## Runtime contracts the skill must not invent

1. There is no `submit_input` tool and no `awr:input` scope on this baseline.
   Planners transmit `feature.plan` and `refinement.plan` through
   `submit_prompt_for_planning`. `bugfix.plan` is a skill-level planning
   intent; the current decorator parser only accepts `feature.plan` and
   `refinement.plan` on that tool.
2. `plan.execute`, `plan.revise`, `question.answer`, `implementation.refine`,
   and `completion.review` are packet or broker-event shapes. They are not
   MCP tools. Adapters or a later `submit_input` path must carry them.
3. `request_plan_approval` / `plan.approval_requested` is a broker event, not
   a stored `record_decision`.
4. Review packet outcome is `APPROVED` | `REVISE` | `REJECTED`. The work-order
   state for `REVISE` is `REVISION_REQUIRED`.
5. The parent field on `awr.response/v1` is `in_reply_to`, not `parent_id`.
   The packet fingerprint field is `content_sha256`.
6. `receipt.accepted` payload requires `receipt_type`, `status`, and
   `content_sha256`. `plan.completed` payload requires `content` and
   `content_sha256`. Payload body hashes may be serialized as `body_sha256`.
7. EX-01 is not merged. Do not claim live Cursor dispatch,
   `refresh_external_run`, durable execution reconciliation, or automatic
   acknowledgement/terminal capture.
8. AS-04 is not merged. Executors receive `not_delivered` references. The
   skill must not claim artifact bytes, GCS delivery, or signed access reach
   an executor.

## What Agent 1 can consume without merging this branch

Static fixtures live at `examples/AWR-GT-003/`. They share one work-order
lineage and were rendered through the current AS-03 parser/renderer. Agent 1
can copy that directory or the generator script. Regenerating fingerprints
requires the baseline `awr.responses` package, not skill-only hashes.

## Suggested EX-01 discovery hooks

When the server lists `refresh_external_run` or an execution-dispatch tool,
the skill already treats those as EX-01. Until then, a request to execute
must stop and name the missing capability.

## Collision report for Agent 1

This branch does not modify `src/awr/service.py`, `src/awr/lifecycle/**`,
`src/awr/executors/**`, `src/awr/storage/**`, `src/awr/transports/**`,
runtime response schemas, artifact intake/scanning/delivery, or deployment
scripts.
