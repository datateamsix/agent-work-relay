# Planner workflow

Load the matching `@input` template from `assets/templates/`. Drafting does
not authorize transmission.

## Sequence

1. Draft a `feature.plan` or `bugfix.plan` work packet.
2. Transmit only when the user asks to send it. On this baseline,
   `submit_prompt_for_planning` accepts `feature.plan` and
   `refinement.plan` only. If the draft is `bugfix.plan`, stop and say
   that intake is missing; do not silently rewrite it as `feature.plan`.
3. Show the durable acceptance receipt. Do not claim success without it.
4. Retrieve the plan with `refresh_planning` or `get_plan`.
5. For a bounded plan change, draft `plan.revise` bound to the plan ID and
   SHA-256. Transmit only through an available input or adapter path.
6. Answer `question.blocked` with `question.answer`. A clarification does not
   authorize execution.
7. Request human plan approval through the broker event
   `plan.approval_requested`, not `record_decision`.
8. After a human approves, the stored decision must cite the exact plan ID
   and SHA-256. A request to plan does not authorize execution.
9. Monitor approved execution only when EX-01 tools are listed. If they are
   not, stop and say execution orchestration is unavailable.
10. After completion, retrieve the completion packet, timeline, and evidence
    references. Request `completion.review` when a recommendation is needed.
11. Human acceptance or revision is `record_decision`. A request to execute
    does not authorize merge, main-branch push, deployment, destructive
    work, or completion acceptance.

## Bindings

- Repository URL and base ref are explicit.
- Acceptance criteria are observable.
- Parent, plan, and source fingerprints come from receipts.
- Idempotency follows [idempotency.md](idempotency.md).

## Tools this role may call

Reads: `get_work_order`, `get_plan`, `get_work_order_timeline`,
`list_pending_actions`.

Mutations, only when listed and authorized: `submit_prompt_for_planning`,
`submit_work_bundle_for_planning`, artifact intake tools.

Do not call `record_decision` as the planning agent unless the user is the
decision principal and asked to record that decision.
