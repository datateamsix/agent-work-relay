# human.decision.request
schema: awr.decision.request/v1
kind: request_plan_approval
work_order_id: wo_gt003_feature_catalog
plan_id: plan_gt003_v1
plan_sha256: b57127b6d04ea32e51637c58592b34c1bcc2ef314ada155fe764aaca0ed17f13
source_input_sha256: f151153165d2bf3f04668a5469c3b4dc199501a5ea9a099a961b70087116a1ad
in_reply_to: msg_gt003_plan_v1
actor: human:gt003.owner
idempotency_key: awr:wo_gt003_feature_catalog:request_plan_approval:plan_gt003_v1

Approve the exact plan identified by plan_id and plan SHA-256. This request
is not itself a stored decision. Transmit only via record_decision after the
human explicitly approves.
