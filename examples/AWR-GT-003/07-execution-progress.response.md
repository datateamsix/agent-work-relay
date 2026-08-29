@response
---
awr:
  schema: awr.response/v1
  response_type: execution.progress
  work_order_id: wo_gt003_feature_catalog
  in_reply_to: msg_gt003_exec_ack
  idempotency_key: awr:wo_gt003_feature_catalog:execution.progress:cursor-cloud-run-gt003-0001:milestone1
  source_input_sha256: sha256:90078ca7903026bfe2b08f341f0cc190f702e379647bfdf907be4f0df7ea7a26
  created_at: 2026-08-29T12:35:00Z
  authority: report_only
  content_sha256: sha256:89e84c713cd259ff2ee5ec3500dd0c15cb158744dfa4716f91ea8379fd869051
  executor_run_id: cursor-cloud-run-gt003-0001
  percent: 60
---

# Execution progress

Search query path and unpublished filter implemented; tests not yet green. Catalog scan may be slow on large inventories.
