# MCP tools and scopes

The skill and the MCP connection are separate. This file maps roles to tools.
[capability.md](capability.md) says which of those tools the current server
may actually expose.

## Operational baseline scopes

| Scope | Tools |
|---|---|
| `awr:plan` | `submit_prompt_for_planning`, `submit_work_bundle_for_planning`, `begin_artifact_intake`, `finalize_artifact_upload` |
| `awr:read` | `get_plan`, `get_work_order`, `get_work_order_timeline`, `list_pending_actions`, `get_artifact_status`, `get_work_order_artifacts` |
| `awr:refresh` | `refresh_planning` |
| `awr:response` | `submit_response` |
| `awr:decide` | `record_decision` |

Prepared EX-01 scope, do not claim it exists before discovery:

| Scope | Expected tool |
|---|---|
| `awr:refresh` | `refresh_external_run` once the server lists it |

There is no `awr:input` or `submit_input` on the `762cbe4` runtime.
`get_work_order` cannot transmit `@input` packets. Only `feature.plan` and
`refinement.plan` have a public mutation path today.

## `submit_prompt_for_planning`

Use for `@input` feature and refinement plans after the user asks to transmit.

Required arguments: decorated Markdown, sender, recipient, repository URL
unless the broker has a default, optional base ref, idempotency key.

## `submit_response`

Accepts one `@response` Markdown document. The packet must parse as
`awr.response/v1` with `authority: report_only`. Bind `work_order_id`,
`in_reply_to`, `source_input_sha256`, and a stable idempotency key. Repeat
`executor_run_id` after acknowledgement.

## `record_decision`

Restricted. Required: `decision_type`, `work_order_id`, `target_id`,
`target_sha256`, `idempotency_key`, `permitted_action`, compact `rationale`
(512 bytes). Optional `expires_at`.

`request_plan_approval` is not a stored decision. It is a broker event.

Allowed stored types on this baseline: `approve_plan`, `reject_plan`,
`accept_completion`, `request_revision`, `cancel`.

Merge, main-branch push, deployment, and destructive authorization remain
separate restricted decisions. Do not invent them in a response packet.

## Reads

`get_work_order` and `list_pending_actions` authorize an explicit participant
when supplied. `list_pending_actions` without a work-order ID is actor-scoped.
Calling it with neither an actor nor a work-order ID is an error.

## Artifact tools

Intake is metadata plus a one-time `PUT /v1/artifacts/{id}/content`. MCP never
accepts bytes. CLEAN status does not mean the executor received the body.

## Compatibility aliases

| Existing tool | Generalized intent |
|---|---|
| `submit_prompt_for_planning` | `@input` feature or refinement plan |
| `submit_work_bundle_for_planning` | same, plus CLEAN artifact IDs |
| `refresh_planning` | planning-run refresh; not EX-01 execution refresh |
| `get_plan` | stored plan packet |
| `@awr feature.plan` | compatibility alias for `@input` + `feature.plan` |
