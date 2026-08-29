@input
---
awr:
  schema: awr.input/v1
  intent: bugfix.plan
  parent_work_order_id: null
  correlation_id: null
  idempotency_key: awr:<client-correlation>:bugfix.plan:v1
  repository:
    url: <https-repository-url>
    base_ref: main
  requested_executor: <cursor|claude-code|gemini|other>
  requested_authority: plan_only
---

# <Defect name>

Required protocol: `schema`, `intent`, `idempotency_key`.
Required lifecycle bindings: repository URL and base ref.
Recommended narrative: observed, expected, reproduction, acceptance.

## Observed behavior

<What happens now, including sanitized evidence.>

## Expected behavior

<What should happen?>

## Reproduction

1. <Step>

## Scope and impact

<Users, data, environments, severity.>

## Constraints

- Preserve <compatibility or invariant>.

## Acceptance criteria

- [ ] Root cause is explained with repository evidence.
- [ ] A regression test fails before and passes after the proposed fix.

## Planning response requested

Return hypotheses, inspection plan, proposed correction, and only genuinely
blocking questions. Do not modify the repository.
