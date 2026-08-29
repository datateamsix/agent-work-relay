@input
---
awr:
  schema: awr.input/v1
  intent: bugfix.plan
  parent_work_order_id: null
  correlation_id: null
  idempotency_key: <stable-key>
  repository:
    url: <https-repository-url>
    base_ref: main
  requested_executor: <cursor|claude-code|gemini|other>
  requested_authority: plan_only
---

# <Defect name>

## Observed behavior

<What happens now, including error or evidence?>

## Expected behavior

<What should happen?>

## Reproduction

1. <Step>

## Scope and impact

<Affected users, data, environments, and severity.>

## Constraints

- Preserve <compatibility or invariant>.

## Acceptance criteria

- [ ] Root cause is explained with repository evidence.
- [ ] A regression test fails before and passes after the proposed fix.
- [ ] <Additional observable condition>

## Planning response requested

Return root-cause hypotheses, inspection plan, proposed correction, regression
coverage, risk, and only genuinely blocking questions. Do not modify the repo.
