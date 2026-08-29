@input
---
awr:
  schema: awr.input/v1
  intent: feature.plan
  parent_work_order_id: null
  correlation_id: null
  idempotency_key: <stable-key>
  repository:
    url: <https-repository-url>
    base_ref: main
  requested_executor: <cursor|claude-code|gemini|other>
  requested_authority: plan_only
---

# <Feature name>

## Outcome

<What should become possible and for whom?>

## Context

<Relevant product, architecture, and user context.>

## Requirements

- <Required behavior>

## Acceptance criteria

- [ ] <Observable pass condition>

## Constraints and non-goals

- <Boundary, compatibility requirement, or deliberate exclusion>

## Relevant artifacts

- <Artifact ID, URI, or none>

## Planning response requested

Return existing architecture, implementation steps, affected files and
contracts, test plan, risks, assumptions, and only genuinely blocking questions.
Do not modify the repository.
