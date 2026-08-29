@input
---
awr:
  schema: awr.input/v1
  intent: completion.review
  parent_work_order_id: wo_gt003_feature_catalog
  correlation_id: corr_gt003_completion_review
  idempotency_key: awr:wo_gt003_feature_catalog:completion.review:cursor-cloud-run-gt003-0001:v1
  plan_id: plan_gt003_v1
  plan_sha256: sha256:b57127b6d04ea32e51637c58592b34c1bcc2ef314ada155fe764aaca0ed17f13
  executor_run_id: cursor-cloud-run-gt003-0001
  completion_id: msg_gt003_exec_completed
---

# Review catalog search completion

Compare the approved plan with the completion packet and evidence. Recommend
APPROVED, REVISE, or REJECTED. Do not close the work order.
