from __future__ import annotations

import unittest
from pathlib import Path

from lc01b_helpers import PLAN_BODY, RECIPIENT, SENDER, LifecycleHarness, plan_payload

from awr.contracts import WorkStatus
from awr.responses.canonical import fingerprint_packet
from awr.responses.contracts import ResponseType
from awr.responses.validate import parse_response_markdown

VALIDATED_PACKET = Path("/opt/cursor/artifacts/lc01b-execution-completed.validated.md")


class LifecycleGoldenPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = LifecycleHarness()

    def tearDown(self) -> None:
        self.harness.close()

    def test_required_execution_sequence(self) -> None:
        work_order_id = self.harness.accept_planning()
        projection = self.harness.projection(work_order_id)
        self.assertEqual(projection["status"], WorkStatus.PLANNING.value)

        markdown, packet, receipt = self.harness.submit(
            response_type=ResponseType.PLAN_COMPLETED,
            work_order_id=work_order_id,
            payload=plan_payload(),
            actor=RECIPIENT,
            idempotency_key="plan-completed-1",
        )
        self.assertFalse(receipt["duplicate"])
        self.assertEqual(receipt["status"], WorkStatus.PLAN_READY.value)
        pending = self.harness.service.list_pending_actions(work_order_id, actor=SENDER)
        self.assertEqual(pending[0]["action"], "plan.approval_requested")
        self.assertEqual(pending[0]["kind"], "broker_event")

        requested = self.harness.service.request_plan_approval(work_order_id, actor=SENDER)
        self.assertEqual(requested["status"], WorkStatus.WAITING_FOR_PLAN_APPROVAL.value)
        pending = self.harness.service.list_pending_actions(work_order_id, actor=SENDER)
        self.assertEqual({item["action"] for item in pending}, {"approve_plan", "reject_plan"})

        lifecycle = self.harness.projection(work_order_id)["lifecycle"]
        approved = self.harness.service.record_decision(
            decision_type="approve_plan",
            work_order_id=work_order_id,
            actor=SENDER,
            target_id=str(lifecycle["plan_id"]),
            target_sha256=str(lifecycle["plan_sha256"]),
            idempotency_key="approve-plan-1",
            permitted_action="plan.execute",
        )
        self.assertEqual(approved["status"], WorkStatus.READY_FOR_EXECUTION.value)

        dispatched = self.harness.service.dispatch_execution(
            work_order_id,
            actor=SENDER,
            plan_id=str(lifecycle["plan_id"]),
            plan_sha256=str(lifecycle["plan_sha256"]),
        )
        self.assertEqual(dispatched["status"], WorkStatus.EXECUTION_DISPATCHED.value)
        self.assertNotEqual(dispatched["status"], WorkStatus.EXECUTING.value)

        _, _, ack = self.harness.submit(
            response_type=ResponseType.EXECUTION_ACKNOWLEDGED,
            work_order_id=work_order_id,
            payload={"executor": "cursor:cloud", "executor_run_id": "run-golden"},
            actor=RECIPIENT,
            idempotency_key="exec-ack-1",
            executor_run_id="run-golden",
        )
        self.assertEqual(ack["status"], WorkStatus.EXECUTING.value)
        self.assertEqual(
            self.harness.projection(work_order_id)["lifecycle"]["bound_run_id"],
            "run-golden",
        )

        _, _, progress = self.harness.submit(
            response_type=ResponseType.EXECUTION_PROGRESS,
            work_order_id=work_order_id,
            payload={"message": "Router inspected", "percent": 40},
            actor=RECIPIENT,
            idempotency_key="exec-progress-1",
            executor_run_id="run-golden",
        )
        self.assertEqual(progress["status"], WorkStatus.EXECUTING.value)

        completed_markdown, completed_packet, completed = self.harness.submit(
            response_type=ResponseType.EXECUTION_COMPLETED,
            work_order_id=work_order_id,
            payload={"summary": "Health endpoint planned and verified."},
            actor=RECIPIENT,
            idempotency_key="exec-completed-1",
            executor_run_id="run-golden",
        )
        self.assertEqual(completed["status"], WorkStatus.COMPLETION_READY.value)
        round_trip = parse_response_markdown(completed_markdown)
        self.assertEqual(round_trip.content_sha256, fingerprint_packet(round_trip))
        self.assertEqual(round_trip.content_sha256, completed_packet.content_sha256)
        self.assertEqual(round_trip.response_type, ResponseType.EXECUTION_COMPLETED)
        VALIDATED_PACKET.parent.mkdir(parents=True, exist_ok=True)
        VALIDATED_PACKET.write_text(completed_markdown, encoding="utf-8")
        self.assertTrue(completed_markdown.startswith("@response"))
        self.assertIn("response_type: execution.completed", completed_markdown)
        self.assertIn("authority: report_only", completed_markdown)

        reviewed = self.harness.service.request_completion_review(work_order_id, actor=SENDER)
        self.assertEqual(reviewed["status"], WorkStatus.PLANNER_REVIEWING.value)

        _, _, recommendation = self.harness.submit(
            response_type=ResponseType.REVIEW_COMPLETED,
            work_order_id=work_order_id,
            payload={"outcome": "APPROVED", "rationale": "The packet matches the approved plan."},
            actor=SENDER,
            idempotency_key="review-1",
        )
        self.assertEqual(recommendation["status"], WorkStatus.WAITING_FOR_HUMAN_REVIEW.value)
        after_review = self.harness.projection(work_order_id)
        self.assertEqual(after_review["status"], WorkStatus.WAITING_FOR_HUMAN_REVIEW.value)
        self.assertEqual(after_review["lifecycle"]["latest_review_outcome"], "APPROVED")
        self.assertNotEqual(after_review["status"], WorkStatus.COMPLETE.value)

        accepted = self.harness.service.record_decision(
            decision_type="accept_completion",
            work_order_id=work_order_id,
            actor=SENDER,
            target_id=str(completed["content_sha256"]),
            target_sha256=str(completed["content_sha256"]),
            idempotency_key="accept-1",
            permitted_action="close",
            target_kind="completion",
        )
        self.assertEqual(accepted["status"], WorkStatus.COMPLETE.value)
        self.assertEqual(
            self.harness.projection(work_order_id)["status"], WorkStatus.COMPLETE.value
        )
        self.assertIn(
            "plan.completed", [entry.event_type for entry in self.store_events(work_order_id)]
        )
        _ = markdown
        _ = packet
        _ = PLAN_BODY

    def store_events(self, work_order_id: str) -> list[object]:
        return list(self.harness.store.list_ledger(work_order_id))


if __name__ == "__main__":
    unittest.main()
