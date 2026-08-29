# human.decision.request
schema: awr.decision.request/v1
kind: accept_completion
work_order_id: wo_gt003_feature_catalog
review_id: rev_gt003_v1
completion_id: msg_gt003_exec_completed
plan_id: plan_gt003_v1
in_reply_to: msg_gt003_review
actor: human:gt003.owner
idempotency_key: awr:wo_gt003_feature_catalog:accept_completion:rev_gt003_v1

Human acceptance of completion. Transmit only via record_decision after the
human explicitly accepts. Agent recommendation grants no authority.
