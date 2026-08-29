from __future__ import annotations

import unittest

from lc01b_helpers import RECIPIENT, SENDER, LifecycleHarness, plan_payload

from awr.contracts import WorkStatus
from awr.responses.canonical import ResponsePacketError
from awr.responses.contracts import ResponseType
from awr.service import WorkOrderValidationError


class AuthorityGuardrailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = LifecycleHarness()
        self.work_order_id = self.harness.accept_planning()
        self.harness.submit(
            response_type=ResponseType.PLAN_COMPLETED,
            work_order_id=self.work_order_id,
            payload=plan_payload(),
            actor=RECIPIENT,
            idempotency_key="plan-a",
        )
        self.harness.service.request_plan_approval(self.work_order_id, actor=SENDER)

    def tearDown(self) -> None:
        self.harness.close()

    def test_execution_requires_stored_plan_approval(self) -> None:
        lifecycle = self.harness.projection(self.work_order_id)["lifecycle"]
        with self.assertRaisesRegex(WorkOrderValidationError, "stored decision"):
            self.harness.service.dispatch_execution(
                self.work_order_id,
                actor=SENDER,
                plan_id=str(lifecycle["plan_id"]),
                plan_sha256=str(lifecycle["plan_sha256"]),
            )

    def test_plan_a_cannot_authorize_plan_b(self) -> None:
        lifecycle = self.harness.projection(self.work_order_id)["lifecycle"]
        self.harness.service.record_decision(
            decision_type="approve_plan",
            work_order_id=self.work_order_id,
            actor=SENDER,
            target_id=str(lifecycle["plan_id"]),
            target_sha256=str(lifecycle["plan_sha256"]),
            idempotency_key="approve-a",
            permitted_action="plan.execute",
        )
        with self.assertRaisesRegex(WorkOrderValidationError, "cannot authorize another"):
            self.harness.service.dispatch_execution(
                self.work_order_id,
                actor=SENDER,
                plan_id="PLAN-OTHER",
                plan_sha256="f" * 64,
            )

    def test_request_plan_approval_is_not_a_stored_decision(self) -> None:
        with self.assertRaisesRegex(WorkOrderValidationError, "not a stored decision"):
            self.harness.service.record_decision(
                decision_type="request_plan_approval",
                work_order_id=self.work_order_id,
                actor=SENDER,
                target_id="PLAN-1",
                target_sha256="b" * 64,
                idempotency_key="not-a-decision",
                permitted_action="approve",
            )

    def test_responses_cannot_grant_authority(self) -> None:
        with self.assertRaisesRegex(ResponsePacketError, "never grant"):
            self.harness.render_parse(
                response_type=ResponseType.PLAN_COMPLETED,
                work_order_id=self.work_order_id,
                payload=plan_payload(plan_id="PLAN-FORGED"),
                actor=RECIPIENT,
                idempotency_key="forged-authority",
                authority="execution",
            )

    def test_review_approval_does_not_close(self) -> None:
        lifecycle = self.harness.projection(self.work_order_id)["lifecycle"]
        self.harness.service.record_decision(
            decision_type="approve_plan",
            work_order_id=self.work_order_id,
            actor=SENDER,
            target_id=str(lifecycle["plan_id"]),
            target_sha256=str(lifecycle["plan_sha256"]),
            idempotency_key="approve-close",
            permitted_action="plan.execute",
        )
        self.harness.service.dispatch_execution(
            self.work_order_id,
            actor=SENDER,
            plan_id=str(lifecycle["plan_id"]),
            plan_sha256=str(lifecycle["plan_sha256"]),
        )
        self.harness.submit(
            response_type=ResponseType.EXECUTION_ACKNOWLEDGED,
            work_order_id=self.work_order_id,
            payload={"executor": "cursor:cloud", "executor_run_id": "run-auth"},
            actor=RECIPIENT,
            idempotency_key="ack-auth",
            executor_run_id="run-auth",
        )
        self.harness.submit(
            response_type=ResponseType.EXECUTION_COMPLETED,
            work_order_id=self.work_order_id,
            payload={"summary": "Done"},
            actor=RECIPIENT,
            idempotency_key="done-auth",
            executor_run_id="run-auth",
        )
        self.harness.service.request_completion_review(self.work_order_id, actor=SENDER)
        receipt = self.harness.submit(
            response_type=ResponseType.REVIEW_COMPLETED,
            work_order_id=self.work_order_id,
            payload={"outcome": "APPROVED", "rationale": "Recommend close."},
            actor=SENDER,
            idempotency_key="review-auth",
        )[2]
        self.assertEqual(receipt["status"], WorkStatus.WAITING_FOR_HUMAN_REVIEW.value)
        self.assertEqual(
            self.harness.projection(self.work_order_id)["status"],
            WorkStatus.WAITING_FOR_HUMAN_REVIEW.value,
        )


if __name__ == "__main__":
    unittest.main()
