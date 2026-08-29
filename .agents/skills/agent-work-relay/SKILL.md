---
name: agent-work-relay
description: Relay product-development work, plans, questions, execution updates, completion evidence, and reviews through Agent Work Relay. Use when a user asks to send, route, hand off, retrieve, review, refine, approve, or respond to agent work. Do not invoke merely to draft a document the user has not asked to transmit. Do not invent MCP tools that the connected server has not listed.
---

# Agent Work Relay

Pass work between planning, coding, and review agents. Preserve human
authority, immutable packets, receipts, and one work-order lineage.

## Identify the role, then load only that path

| Role | Load |
|---|---|
| Planning agent | [references/planner-workflows.md](references/planner-workflows.md) |
| Coding or execution agent | [references/worker-workflows.md](references/worker-workflows.md) |
| Review agent | [references/reviewer-workflows.md](references/reviewer-workflows.md) |
| Broker or trusted adapter | [references/adapter-workflows.md](references/adapter-workflows.md) |
| Human decision-maker | [references/human-decisions.md](references/human-decisions.md) |

Shared rules, loaded only when needed:

- Lifecycle and states: [references/lifecycle.md](references/lifecycle.md)
- Decorators and envelopes: [references/decorators.md](references/decorators.md)
- MCP tools and scopes: [references/mcp-tools.md](references/mcp-tools.md)
- Capability gating: [references/capability.md](references/capability.md)
- Idempotency and lineage: [references/idempotency.md](references/idempotency.md)
- Client installation: [references/installation.md](references/installation.md)
- Template customization: [references/customization.md](references/customization.md)

Do not load every reference. Instantiate one template from `assets/templates/`.

## Protocol

The first nonblank line is exactly one of:

```text
@input
@response
```

Lifecycle meaning lives in the typed `awr` envelope. Do not invent another
top-level decorator. Neither decorator grants authority. Every `@response`
carries `authority: report_only`.

## Before any mutation

1. Confirm the user asked to transmit, not only draft.
2. List the tools the connected AWR server actually exposes.
3. If a required tool is missing, stop and name the missing capability.
   Do not silently turn execution into planning or plain text.
4. Bind work-order, parent, plan, run, and fingerprint values from broker
   receipts. Never guess a broker-issued identifier.
5. Submit only through an available tool. Claim success only after a
   confirmed broker receipt.

See [references/capability.md](references/capability.md) for what is
operational on this baseline versus EX-01 and AS-04.
