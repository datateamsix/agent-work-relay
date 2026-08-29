# Capability gating

Inspect the connected server's tool list before every mutation. A skill
template is not proof that a tool exists.

Never claim that a relay, execution, artifact delivery, or decision succeeded
without a confirmed broker receipt.

## Baseline on `762cbe4`

These tools are operational when the server lists them:

| Tool | Scope | Use |
|---|---|---|
| `submit_prompt_for_planning` | `awr:plan` | Markdown planning intake |
| `submit_work_bundle_for_planning` | `awr:plan` | Planning plus CLEAN artifact IDs |
| `begin_artifact_intake` | `awr:plan` | Declare metadata and get an upload ticket |
| `finalize_artifact_upload` | `awr:plan` | Run the security gate |
| `get_artifact_status` | `awr:read` | Artifact metadata only |
| `get_work_order_artifacts` | `awr:read` | Immutable reference list, not bytes |
| `refresh_planning` | `awr:refresh` | Capture a terminal planning run |
| `get_plan` | `awr:read` | Return the stored plan packet |
| `submit_response` | `awr:response` | Persist an `awr.response/v1` packet |
| `record_decision` | `awr:decide` | Store human or policy authority |
| `get_work_order` | `awr:read` | Projection, snapshot, pending actions |
| `get_work_order_timeline` | `awr:read` | Ordered ledger |
| `list_pending_actions` | `awr:read` | Actor-scoped or work-order pending set |

LC-01B execution and review transitions are operational on this baseline
through `submit_response` and `record_decision`. `plan.execute` is a broker
event (`dispatch_execution` / adapter), not a stored decision.

There is no `submit_input` tool on this baseline. Planners transmit
`feature.plan` and `refinement.plan` through `submit_prompt_for_planning`.
These `@input` intents are prepared until a mutation tool can accept them:

| Intent | Missing capability |
|---|---|
| `bugfix.plan` | `submit_input` |
| `plan.revise` | `submit_input` |
| `question.answer` | `submit_input` |
| `completion.review` | `submit_input` |
| `plan.execute` | `submit_input` and AWR-EX-01 dispatch |
| `implementation.refine` | `submit_input` and AWR-EX-01 dispatch |

`get_work_order` is read-only. It cannot transmit those packets. Do not
rewrite `bugfix.plan` as `feature.plan`.

## Prepared, not operational until EX-01

Treat these as unavailable unless the server lists them:

- real Cursor execution dispatch beyond the recording adapter;
- `refresh_external_run`;
- durable provider execution reconciliation;
- automatic execution acknowledgement and terminal-result capture.

If the user asked to execute and those tools are missing, stop. Do not
downgrade the request into a planning run or a prose summary.

## Unavailable until AS-04

Do not claim any of the following:

- delivery of CLEAN artifact bytes to an executor;
- GCS clean-object delivery;
- signed artifact access;
- capability-aware binary materialization;
- artifact delivery acknowledgement.

Executors receive a reference manifest marked `not_delivered`. MCP arguments
never include file bytes, base64 bodies, or remote fetch URLs.

## Discovery rule

1. List tools.
2. Match the intended mutation to a listed tool and scope.
3. If the tool is absent, name the missing capability (`baseline`, `EX-01`,
   or `AS-04`) and wait.
4. After a call, show the receipt. No receipt means the mutation did not
   happen.
