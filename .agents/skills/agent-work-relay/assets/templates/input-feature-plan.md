@input
---
awr:
  schema: awr.input/v1
  intent: feature.plan
  parent_work_order_id: null
  correlation_id: null
  idempotency_key: awr:<client-correlation>:feature.plan:v1
  repository:
    url: <https-repository-url>
    base_ref: main
  requested_executor: <cursor|claude-code|gemini|other>
  requested_authority: plan_only
---

# <Feature name>

Required protocol: `schema`, `intent`, `idempotency_key`.
Required lifecycle bindings: repository URL and base ref for code work.
Recommended narrative: the sections below. Optional provider: executor hint.

## Outcome

<What should become possible and for whom?>

## Context

<Relevant product and architecture context. Do not paste the full repository.>

## Requirements

- <Required behavior>

## Acceptance criteria

- [ ] <Observable pass condition>

## Constraints and non-goals

- <Boundary or deliberate exclusion>

## Relevant artifacts

- <Artifact ID or none. Do not embed file bytes.>

## Planning response requested

Return a compact `plan.completed` packet. Do not modify the repository.
This draft does not authorize transmission, execution, merge, or deploy.
