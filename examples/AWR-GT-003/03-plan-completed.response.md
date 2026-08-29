@response
---
awr:
  schema: awr.response/v1
  response_type: plan.completed
  work_order_id: wo_gt003_feature_catalog
  in_reply_to: msg_gt003_receipt_feature
  idempotency_key: awr:wo_gt003_feature_catalog:plan.completed:v1
  source_input_sha256: sha256:f151153165d2bf3f04668a5469c3b4dc199501a5ea9a099a961b70087116a1ad
  created_at: 2026-08-29T12:05:00Z
  authority: report_only
  content_sha256: sha256:b57127b6d04ea32e51637c58592b34c1bcc2ef314ada155fe764aaca0ed17f13
  body_sha256: sha256:9c0c292b17142afc4ee8242ddc871d32fb859edf915794eba49fe1074859e1dd
  title: Catalog search plan
  plan_id: plan_gt003_v1
---

# Catalog search plan

Add in-process catalog search with stable pagination for published products only. Extend the existing catalog query service. Do not introduce a new index vendor.

Steps: 1) add search query parameters 2) filter unpublished products 3) paginate deterministically 4) add API and unit tests.

Contracts: GET /catalog/search. Tests: empty, match, unpublished, pagination stability. Residual risk: naive scan on large catalogs.
