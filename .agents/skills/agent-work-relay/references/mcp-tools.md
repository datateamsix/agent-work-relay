# MCP surface for the bidirectional lifecycle

## Minimal generalized tools

| Tool | Caller | Purpose | Suggested scope |
|---|---|---|---|
| `submit_input` | Planner or reviewer | Accept an `@input` message and route it | `awr:input` |
| `submit_response` | Worker, adapter, or reviewer | Accept an `@response` packet | `awr:response` |
| `get_work_order` | Authorized participant | Return current projection and immutable refs | `awr:read` |
| `get_work_order_timeline` | Authorized participant | Return ordered receipts and decisions | `awr:read` |
| `list_pending_actions` | Authorized participant | Find questions, approvals, reviews, or work | `awr:read` |
| `record_decision` | Human or authorized policy actor | Approve, reject, request revision, cancel | `awr:decide` |
| `refresh_external_run` | Broker operator or task worker | Reconcile provider run state | `awr:refresh` |

Keep the mutation surface small. Lifecycle meaning belongs in typed message
envelopes and the domain state machine rather than proliferating one MCP tool
per template.

## `submit_input`

Required arguments:

- canonical decorated Markdown;
- requested recipient or executor binding;
- repository and base reference when code work is involved;
- idempotency key;
- optional parent work-order, plan, question, review, and artifact references.

The broker authenticates the caller, parses the envelope, validates state and
authority, fingerprints the exact bytes, persists the message and ledger entry,
routes it, and returns a durable receipt.

## `submit_response`

Required arguments:

- canonical decorated Markdown;
- work-order ID and immediate parent reference;
- external agent and run identifiers when applicable;
- source input fingerprint;
- idempotency key;
- optional evidence and artifact references.

The broker verifies participant identity and lineage, fingerprints the response,
applies the state transition, records the receipt, and makes the result available
to the originator.

## `record_decision`

Decisions are structured and restricted. Bind them to the exact object under
review:

- decision type and outcome;
- work-order and expected version;
- plan, completion, or review ID and SHA-256;
- permitted action and scope;
- actor identity and rationale;
- expiry when appropriate;
- idempotency key.

The message decorator never substitutes for this call.

## Compatibility

Server v0.1 planning tools map as follows:

| Existing tool | Generalized equivalent |
|---|---|
| `submit_prompt_for_planning` | `submit_input` with feature/refinement plan |
| `submit_work_bundle_for_planning` | `submit_input` plus artifact references |
| `begin_artifact_intake` | artifact declaration + upload ticket |
| `finalize_artifact_upload` | security-gate inspect |
| `get_artifact_status` | artifact metadata read |
| `get_work_order_artifacts` | immutable reference list |
| `refresh_planning` | `refresh_external_run` |
| `get_plan` | `get_work_order` result selection |
| `get_work_order_timeline` | unchanged |

Supporting binaries use authenticated `PUT /v1/artifacts/{id}/content` with
`X-AWR-Upload-Token`. Do not place file bytes, base64 payloads, or remote URLs
in MCP tool arguments.

`awr.response/v1` packet schemas and `@response` Markdown rendering are defined
for later slices. Do not call `submit_response` and do not treat execution or
review templates as operational until AWR-LC-01 adds those tools, scopes, and
state transitions.
