from __future__ import annotations

import unittest

from lc01b_helpers import RECIPIENT, REPOSITORY, SENDER, LifecycleHarness, plan_payload

from awr.contracts import WorkStatus
from awr.responses.contracts import ResponseType

INPUT_FEATURE = """@input
---
awr:
  schema: awr.input/v1
  intent: feature.plan
  parent_work_order_id: null
  correlation_id: null
  idempotency_key: gt003-input
  repository:
    url: https://github.com/example/project
    base_ref: main
  requested_executor: cursor
  requested_authority: plan_only
---

# Add a health endpoint

Produce an implementation plan. Do not edit files.
"""

EXPECTED_TIMELINE = [
    "work_order.accepted",
    "work_order.routed",
    "executor.acknowledged",
    "plan.completed",
    "plan.approval_requested",
    "decision.approve_plan",
    "plan.execute",
    "execution.acknowledged",
    "execution.progress",
    "execution.completed",
    "completion.review",
    "review.completed",
    "decision.accept_completion",
]


class GoldenGt003Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = LifecycleHarness()

    def tearDown(self) -> None:
        self.harness.close()

    def test_recording_adapter_reaches_complete_and_replays(self) -> None:
        submitted = self.harness.service.submit_prompt_for_planning(
            markdown=INPUT_FEATURE,
            sender=SENDER,
            recipient=RECIPIENT,
            repository_url=REPOSITORY,
            idempotency_key="gt003-input",
        )
        work_order_id = submitted.work_order_id
        self.assertEqual(submitted.status, WorkStatus.PLANNING)

        _, _, planned = self.harness.submit(
            response_type=ResponseType.PLAN_COMPLETED,
            work_order_id=work_order_id,
            payload=plan_payload(),
            actor=RECIPIENT,
            idempotency_key="gt003-plan",
        )
        self.assertEqual(planned["status"], WorkStatus.PLAN_READY.value)

        requested = self.harness.service.request_plan_approval(work_order_id, actor=SENDER)
        self.assertEqual(requested["status"], WorkStatus.WAITING_FOR_PLAN_APPROVAL.value)

        lifecycle = self.harness.projection(work_order_id)["lifecycle"]
        approved = self.harness.service.record_decision(
            decision_type="approve_plan",
            work_order_id=work_order_id,
            actor=SENDER,
            target_id=str(lifecycle["plan_id"]),
            target_sha256=str(lifecycle["plan_sha256"]),
            idempotency_key="gt003-approve",
            permitted_action="plan.execute",
            rationale="Approve the exact stored plan fingerprint.",
        )
        self.assertEqual(approved["status"], WorkStatus.READY_FOR_EXECUTION.value)

        acknowledged = self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        self.assertEqual(acknowledged["response_type"], "execution.acknowledged")
        self.assertEqual(acknowledged["status"], WorkStatus.EXECUTING.value)

        progress = self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        self.assertEqual(progress["response_type"], "execution.progress")

        completed = self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        self.assertEqual(completed["response_type"], "execution.completed")
        self.assertEqual(completed["status"], WorkStatus.COMPLETION_READY.value)
        self.assertNotEqual(completed["status"], WorkStatus.COMPLETE.value)

        reviewed = self.harness.service.request_completion_review(work_order_id, actor=SENDER)
        self.assertEqual(reviewed["status"], WorkStatus.PLANNER_REVIEWING.value)

        review_markdown, _, recommendation = self.harness.submit(
            response_type=ResponseType.REVIEW_COMPLETED,
            work_order_id=work_order_id,
            payload={
                "outcome": "APPROVED",
                "rationale": "Implementation matches the approved plan.",
            },
            actor=SENDER,
            idempotency_key="gt003-review",
        )
        self.assertEqual(recommendation["status"], WorkStatus.WAITING_FOR_HUMAN_REVIEW.value)

        accepted = self.harness.service.record_decision(
            decision_type="accept_completion",
            work_order_id=work_order_id,
            actor=SENDER,
            target_id=str(completed["content_sha256"]),
            target_sha256=str(completed["content_sha256"]),
            idempotency_key="gt003-accept",
            permitted_action="close",
            target_kind="completion",
            rationale="Accept the completed implementation.",
        )
        self.assertEqual(accepted["status"], WorkStatus.COMPLETE.value)

        timeline = [
            entry["event_type"]
            for entry in self.harness.service.get_work_order_timeline(work_order_id, actor=SENDER)
        ]
        self.assertEqual(timeline, EXPECTED_TIMELINE)
        self.assertEqual(len(self.harness.service.executor.execution_dispatches), 1)

        replay_ack = self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        self.assertTrue(replay_ack["duplicate"])
        replay_approve = self.harness.service.record_decision(
            decision_type="approve_plan",
            work_order_id=work_order_id,
            actor=SENDER,
            target_id=str(lifecycle["plan_id"]),
            target_sha256=str(lifecycle["plan_sha256"]),
            idempotency_key="gt003-approve",
            permitted_action="plan.execute",
            rationale="Approve the exact stored plan fingerprint.",
        )
        self.assertEqual(replay_approve["decision_id"], approved["decision_id"])
        replay_review = self.harness.service.submit_response(markdown=review_markdown, actor=SENDER)
        self.assertEqual(replay_review["content_sha256"], recommendation["content_sha256"])
        replay_accept = self.harness.service.record_decision(
            decision_type="accept_completion",
            work_order_id=work_order_id,
            actor=SENDER,
            target_id=str(completed["content_sha256"]),
            target_sha256=str(completed["content_sha256"]),
            idempotency_key="gt003-accept",
            permitted_action="close",
            target_kind="completion",
            rationale="Accept the completed implementation.",
        )
        self.assertEqual(replay_accept["decision_id"], accepted["decision_id"])
        replay_timeline = [
            entry["event_type"]
            for entry in self.harness.service.get_work_order_timeline(work_order_id, actor=SENDER)
        ]
        self.assertEqual(replay_timeline, EXPECTED_TIMELINE)
        self.assertEqual(len(self.harness.service.executor.execution_dispatches), 1)
        self.assertEqual(
            self.harness.projection(work_order_id)["status"], WorkStatus.COMPLETE.value
        )


if __name__ == "__main__":
    unittest.main()
