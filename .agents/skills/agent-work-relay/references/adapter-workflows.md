# Broker and trusted-provider adapter workflow

Adapters submit worker packets the worker cannot send itself. They do not
invent lifecycle meaning.

## Duties

1. Discover available MCP tools before each mutation.
2. Read the work-order projection and timeline.
3. Extract exactly one `@response` from the provider result.
4. Validate it with the AS-03 parser (`awr.response/v1`).
5. Submit through `submit_response` when that tool is listed.
6. Return the broker receipt to the originator.
7. For planning runs on this baseline, use `refresh_planning` to capture a
   terminal plan once.

## Adapter-return contract

- The worker may only return a compact packet.
- The adapter is the AWR client.
- If validation fails, do not coerce the packet into another response type.
- If `submit_response` is missing, stop and report the missing baseline
  capability.

## EX-01 preparation

When the server lists execution-orchestration tools, the adapter may:

- dispatch a real provider run after stored plan approval;
- call `refresh_external_run`;
- reconcile durable provider state;
- submit `execution.acknowledged` and terminal packets on the worker's
  behalf.

Until those tools are listed, do not pretend the recording planner is a
live execution orchestrator.

## AS-04

Do not deliver artifact bytes to an executor. Pass references only.
