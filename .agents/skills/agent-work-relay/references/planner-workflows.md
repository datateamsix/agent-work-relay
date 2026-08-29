# Planner and reviewer workflows

## Send a new work item

1. Confirm the user asked to transmit the work, not merely draft it.
2. Select `feature.plan` or `bugfix.plan` and load only its template.
3. Make acceptance criteria observable and distinguish requirements from
   preferences.
4. Bind the repository and base reference explicitly.
5. Submit through AWR and show the acceptance receipt.

## Refine a plan

Use `refinement.plan` when the product request itself changed or more discovery
is needed. Use `plan.revise` when reviewing a specific returned plan. Bind the
parent work-order ID and plan ID; preserve the prior plan.

## Answer a worker question

Use `question.answer`. Answer only the cited question IDs. State whether the
answer changes scope, acceptance criteria, or authority. A product clarification
does not authorize execution unless a separate approval record says so.

## Authorize execution

First retrieve the plan and timeline. Verify the plan fingerprint, repository,
base reference, requested scope, and unresolved questions. Record the human
decision through the restricted decision tool. Only then submit `plan.execute`
with the broker-issued approval reference.

## Review completion

Load `input-completion-review.md` and the returned completion packet. Check:

- approved scope versus actual changes;
- commit, branch, pull request, or main-branch refs;
- tests and other verification evidence;
- omitted, failed, or unverified acceptance criteria;
- security, migration, rollout, and rollback risks;
- unexpected repository or infrastructure mutations;
- remaining questions and follow-up work.

Return `review.completed` with `APPROVED`, `REVISION_REQUIRED`, or `REJECTED`.
Do not approve solely because a worker says tests passed; prefer linked or
reproducible evidence.
