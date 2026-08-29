# Reviewer workflow

A reviewing agent recommends. It cannot close a work order.

## Sequence

1. Retrieve the approved plan (`get_plan` or the stored plan fingerprint).
2. Retrieve the execution completion packet and evidence references.
3. Inspect work-order lineage and the receipt timeline.
4. Compare approved scope with reported changes.
5. Evaluate each acceptance criterion.
6. Evaluate tests and cited evidence. Do not approve only because the
   worker said tests passed.
7. Identify unauthorized or unexpected mutations.
8. Report `APPROVED`, `REVISE`, or `REJECTED` in `review.completed`.
9. Keep `authority: report_only`.

`REVISE` is the packet outcome. The work-order state becomes
`REVISION_REQUIRED` when the broker applies it or a human records
`request_revision`.

## Must include

- recommendation;
- evidence reviewed;
- bounded findings;
- acceptance-criteria assessment;
- residual risk;
- an explicit statement that the review grants no authority.

## Must not

- accept completion;
- merge, push to main, or deploy;
- approve the reviewer's own implementation;
- embed complete logs or diffs.

Only a stored human or authorized policy `accept_completion` decision
closes the work order.
