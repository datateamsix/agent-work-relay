@input
---
awr:
  schema: awr.input/v1
  intent: feature.plan
  parent_work_order_id: null
  correlation_id: corr_gt003_feature_plan
  idempotency_key: awr:wo_gt003_feature_catalog:feature.plan:v1
  repository:
    url: https://github.com/example/fixture-relay
    base_ref: main
    base_sha: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  requested_executor: cursor
  requested_authority: plan_only
---

# Catalog search

## Outcome

Published products can be searched with stable pagination.

## Context

Existing catalog query service. Do not change checkout or payment.

## Requirements

- GET /catalog/search returns matching published products
- Results are paginated and stable for a given query
- Unpublished products never appear

## Acceptance criteria

- [ ] Empty query returns an empty page
- [ ] Matching query returns published products only
- [ ] Unpublished products are excluded
- [ ] Pagination is stable for the same query

## Constraints and non-goals

- Do not introduce a new search vendor
- Do not change checkout or payment flows

## Relevant artifacts

- none

## Planning response requested

Return a compact `plan.completed` packet. Do not modify the repository.
This draft does not authorize transmission, execution, merge, or deploy.
