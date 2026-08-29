from __future__ import annotations

import hashlib
import unittest
from datetime import UTC, datetime, timedelta

from lc01b_helpers import FEATURE, RECIPIENT, REPOSITORY, SENDER, LifecycleHarness, plan_payload

from awr.contracts import WorkAction, WorkKind, WorkOrder, WorkStatus
from awr.decorators import parse_directive
from awr.executors.recording_cursor import RecordingCursorExecutor
from awr.lifecycle.decisions import MAX_DECISION_RATIONALE_BYTES, fingerprint_decision
from awr.responses.contracts import ResponseType
from awr.service import BrokerService, WorkOrderValidationError
from awr.storage.firestore import FirestoreStateStore
from awr.storage.firestore_memory import InMemoryFirestore
from awr.wrappers import wrap_prompt

STRANGER = "intruder:observer"
OTHER_SENDER = "chatgpt:other-planner"
OTHER_RECIPIENT = "cursor:other-worker"


def _store_work_order(store: FirestoreStateStore, key: str) -> WorkOrder:
    work_order_id = f"AWR-{key.replace('_', '-')}"
    wrapped = wrap_prompt(parse_directive(FEATURE), FEATURE, work_order_id)
    work_order = WorkOrder(
        work_order_id=work_order_id,
        idempotency_key=key,
        sender=SENDER,
        recipient=RECIPIENT,
        kind=WorkKind.FEATURE,
        action=WorkAction.PLAN,
        parent_work_order_id=None,
        repository_url=REPOSITORY,
        base_ref="main",
        markdown=FEATURE,
        content_sha256=hashlib.sha256(FEATURE.encode("utf-8")).hexdigest(),
        wrapper_id=wrapped.wrapper_id,
        wrapper_version=wrapped.wrapper_version,
        wrapper_sha256=wrapped.wrapper_sha256,
        status=WorkStatus.ACCEPTED,
        created_at=datetime.now(UTC).isoformat(),
    )
    stored, created, _ = store.create_work_order(work_order)
    assert created
    return stored


class AuthorizationHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = LifecycleHarness()
        self.work_order_id = self.harness.accept_planning()

    def tearDown(self) -> None:
        self.harness.close()

    def test_stranger_cannot_read_work_order_or_pending(self) -> None:
        with self.assertRaisesRegex(WorkOrderValidationError, "not authorized"):
            self.harness.service.get_work_order(self.work_order_id, actor=STRANGER)
        with self.assertRaisesRegex(WorkOrderValidationError, "not authorized"):
            self.harness.service.list_pending_actions(self.work_order_id, actor=STRANGER)
        with self.assertRaisesRegex(WorkOrderValidationError, "not authorized"):
            self.harness.service.get_plan(self.work_order_id, actor=STRANGER)
        with self.assertRaisesRegex(WorkOrderValidationError, "not authorized"):
            self.harness.service.get_work_order_timeline(self.work_order_id, actor=STRANGER)
        with self.assertRaisesRegex(WorkOrderValidationError, "not authorized"):
            self.harness.service.get_work_order_artifacts(self.work_order_id, actor=STRANGER)

    def test_list_pending_requires_actor_or_work_order_id(self) -> None:
        with self.assertRaisesRegex(WorkOrderValidationError, "authenticated actor"):
            self.harness.service.list_pending_actions()

    def test_list_pending_is_actor_scoped(self) -> None:
        self.harness.submit(
            response_type=ResponseType.PLAN_COMPLETED,
            work_order_id=self.work_order_id,
            payload=plan_payload(),
            actor=RECIPIENT,
            idempotency_key="scope-plan-a",
        )
        other = self.harness.service.submit_prompt_for_planning(
            markdown=FEATURE,
            sender=OTHER_SENDER,
            recipient=OTHER_RECIPIENT,
            repository_url=REPOSITORY,
            idempotency_key="other-planner-wo",
        )
        other_harness = LifecycleHarness.__new__(LifecycleHarness)
        other_harness.service = self.harness.service
        other_harness.store = self.harness.store
        other_harness.clock = 10
        other_harness.submit(
            response_type=ResponseType.PLAN_COMPLETED,
            work_order_id=other.work_order_id,
            payload=plan_payload(plan_id="PLAN-OTHER"),
            actor=OTHER_RECIPIENT,
            idempotency_key="scope-plan-b",
        )
        mine = {
            item["work_order_id"]
            for item in self.harness.service.list_pending_actions(actor=SENDER)
        }
        theirs = {
            item["work_order_id"]
            for item in self.harness.service.list_pending_actions(actor=OTHER_SENDER)
        }
        self.assertEqual(mine, {self.work_order_id})
        self.assertEqual(theirs, {other.work_order_id})

    def test_recipient_cannot_record_human_decisions(self) -> None:
        self.harness.submit(
            response_type=ResponseType.PLAN_COMPLETED,
            work_order_id=self.work_order_id,
            payload=plan_payload(),
            actor=RECIPIENT,
            idempotency_key="plan-exec-auth",
        )
        self.harness.service.request_plan_approval(self.work_order_id, actor=SENDER)
        lifecycle = self.harness.projection(self.work_order_id)["lifecycle"]
        with self.assertRaisesRegex(WorkOrderValidationError, "cannot record human decisions"):
            self.harness.service.record_decision(
                decision_type="approve_plan",
                work_order_id=self.work_order_id,
                actor=RECIPIENT,
                target_id=str(lifecycle["plan_id"]),
                target_sha256=str(lifecycle["plan_sha256"]),
                idempotency_key="executor-approve",
                permitted_action="plan.execute",
                rationale="Executor identities cannot approve.",
            )

    def test_bound_agent_cannot_record_human_decisions(self) -> None:
        self.harness.submit(
            response_type=ResponseType.PLAN_COMPLETED,
            work_order_id=self.work_order_id,
            payload=plan_payload(),
            actor=RECIPIENT,
            idempotency_key="plan-bound-auth",
        )
        self.harness.service.request_plan_approval(self.work_order_id, actor=SENDER)
        lifecycle = self.harness.projection(self.work_order_id)["lifecycle"]
        self.harness.service.record_decision(
            decision_type="approve_plan",
            work_order_id=self.work_order_id,
            actor=SENDER,
            target_id=str(lifecycle["plan_id"]),
            target_sha256=str(lifecycle["plan_sha256"]),
            idempotency_key="approve-bound",
            permitted_action="plan.execute",
            rationale="Approve the stored plan fingerprint.",
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
            payload={"executor": "cursor:cloud", "executor_run_id": "run-bound"},
            actor=RECIPIENT,
            idempotency_key="ack-bound",
            executor_run_id="run-bound",
        )
        bound = self.harness.projection(self.work_order_id)["lifecycle"]["bound_agent_id"]
        self.assertEqual(bound, RECIPIENT)
        with self.assertRaisesRegex(WorkOrderValidationError, "cannot record human decisions"):
            self.harness.service.record_decision(
                decision_type="cancel",
                work_order_id=self.work_order_id,
                actor=str(bound),
                target_id=self.work_order_id,
                target_sha256=str(lifecycle["source_input_sha256"]),
                idempotency_key="bound-cancel",
                permitted_action="cancel",
                target_kind="work_order",
                rationale="Bound executors cannot cancel.",
            )


class DecisionContractHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = LifecycleHarness()
        self.work_order_id = self.harness.accept_planning()
        self.harness.submit(
            response_type=ResponseType.PLAN_COMPLETED,
            work_order_id=self.work_order_id,
            payload=plan_payload(),
            actor=RECIPIENT,
            idempotency_key="plan-decision-contract",
        )
        self.harness.service.request_plan_approval(self.work_order_id, actor=SENDER)
        self.lifecycle = self.harness.projection(self.work_order_id)["lifecycle"]

    def tearDown(self) -> None:
        self.harness.close()

    def _approve(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "decision_type": "approve_plan",
            "work_order_id": self.work_order_id,
            "actor": SENDER,
            "target_id": str(self.lifecycle["plan_id"]),
            "target_sha256": str(self.lifecycle["plan_sha256"]),
            "idempotency_key": "approve-contract",
            "permitted_action": "plan.execute",
            "rationale": "Approve the stored plan fingerprint.",
        }
        payload.update(overrides)
        return self.harness.service.record_decision(**payload)  # type: ignore[arg-type]

    def test_rationale_is_required_and_compact(self) -> None:
        with self.assertRaisesRegex(WorkOrderValidationError, "rationale is required"):
            self._approve(rationale="   ")
        with self.assertRaisesRegex(WorkOrderValidationError, "compact limit"):
            self._approve(rationale="x" * (MAX_DECISION_RATIONALE_BYTES + 1))

    def test_expired_decision_is_rejected(self) -> None:
        past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        with self.assertRaisesRegex(WorkOrderValidationError, "expired"):
            self._approve(expires_at=past, idempotency_key="approve-expired")

    def test_exact_receipt_replay_and_full_fingerprint(self) -> None:
        expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        first = self._approve(expires_at=expires)
        stored = self.harness.projection(self.work_order_id)["decisions"][0]
        replay = self._approve(expires_at=stored["expires_at"])
        self.assertEqual(replay, first)
        self.assertFalse(first.get("duplicate", False))
        self.assertEqual(fingerprint_decision(stored), first["fingerprint"])
        with self.assertRaisesRegex(WorkOrderValidationError, "already bound"):
            self._approve(
                rationale="A different compact rationale.",
                expires_at=stored["expires_at"],
            )


class WaitingForInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = LifecycleHarness()
        self.work_order_id = self.harness.accept_planning()

    def tearDown(self) -> None:
        self.harness.close()

    def test_question_blocked_uses_waiting_for_input(self) -> None:
        _, _, receipt = self.harness.submit(
            response_type=ResponseType.QUESTION_BLOCKED,
            work_order_id=self.work_order_id,
            payload={"questions": [{"id": "q1", "text": "Which API?"}]},
            actor=RECIPIENT,
            idempotency_key="blocked-1",
        )
        self.assertEqual(receipt["status"], WorkStatus.WAITING_FOR_INPUT.value)
        projection = self.harness.projection(self.work_order_id)
        self.assertEqual(projection["status"], WorkStatus.WAITING_FOR_INPUT.value)
        self.assertEqual(projection["lifecycle"]["blocked_from"], WorkStatus.PLANNING.value)
        pending = self.harness.service.list_pending_actions(self.work_order_id, actor=SENDER)
        self.assertEqual([item["action"] for item in pending], ["question.answer"])
        answered = self.harness.service.answer_question(self.work_order_id, actor=SENDER)
        self.assertEqual(answered["status"], WorkStatus.PLANNING.value)


class ReplayAndConcurrencyTests(unittest.TestCase):
    def test_sqlite_response_replay_returns_original_receipt(self) -> None:
        harness = LifecycleHarness()
        try:
            work_order_id = harness.accept_planning()
            markdown, _, first = harness.submit(
                response_type=ResponseType.PLAN_COMPLETED,
                work_order_id=work_order_id,
                payload=plan_payload(),
                actor=RECIPIENT,
                idempotency_key="sqlite-replay",
            )
            replay = harness.service.submit_response(markdown=markdown, actor=RECIPIENT)
            self.assertEqual(replay, first)
            self.assertFalse(replay["duplicate"])
            self.assertEqual(replay["ledger_sequence"], first["ledger_sequence"])
        finally:
            harness.close()

    def test_firestore_response_and_decision_replay(self) -> None:
        store = FirestoreStateStore(InMemoryFirestore())
        service = BrokerService(store, RecordingCursorExecutor())
        receipt = service.submit_prompt_for_planning(
            markdown=FEATURE,
            sender=SENDER,
            recipient=RECIPIENT,
            repository_url=REPOSITORY,
            idempotency_key="fs-replay-plan",
        )
        harness = LifecycleHarness.__new__(LifecycleHarness)
        harness.service = service
        harness.store = store
        harness.clock = 0
        markdown, _, first = harness.submit(
            response_type=ResponseType.PLAN_COMPLETED,
            work_order_id=receipt.work_order_id,
            payload=plan_payload(),
            actor=RECIPIENT,
            idempotency_key="fs-plan-replay",
        )
        replay = service.submit_response(markdown=markdown, actor=RECIPIENT)
        self.assertEqual(replay, first)
        service.request_plan_approval(receipt.work_order_id, actor=SENDER)
        lifecycle = service.get_work_order(receipt.work_order_id, actor=SENDER)["lifecycle"]
        approved = service.record_decision(
            decision_type="approve_plan",
            work_order_id=receipt.work_order_id,
            actor=SENDER,
            target_id=str(lifecycle["plan_id"]),
            target_sha256=str(lifecycle["plan_sha256"]),
            idempotency_key="fs-approve-replay",
            permitted_action="plan.execute",
            rationale="Approve the stored plan fingerprint.",
        )
        again = service.record_decision(
            decision_type="approve_plan",
            work_order_id=receipt.work_order_id,
            actor=SENDER,
            target_id=str(lifecycle["plan_id"]),
            target_sha256=str(lifecycle["plan_sha256"]),
            idempotency_key="fs-approve-replay",
            permitted_action="plan.execute",
            rationale="Approve the stored plan fingerprint.",
        )
        self.assertEqual(again, approved)

    def test_firestore_conflict_uses_event_identity_not_type(self) -> None:
        store = FirestoreStateStore(InMemoryFirestore())
        work_order = _store_work_order(store, "fs-identity-conflict")
        with store.lock_work_order(work_order.work_order_id) as session:
            first = session.append_ledger(
                "plan.completed",
                work_order.recipient,
                "broker:awr",
                {"message_id": "MSG-A"},
            )
        with (
            self.assertRaisesRegex(RuntimeError, "Concurrent ledger update"),
            store.lock_work_order(work_order.work_order_id) as session,
        ):
            session._base_sequence = first.sequence - 1
            session.append_ledger(
                "plan.completed",
                work_order.recipient,
                "broker:awr",
                {"message_id": "MSG-B"},
            )
        packet = {
            "packet_id": "MSG-PLAN",
            "response_type": "plan.completed",
            "actor": work_order.recipient,
            "idempotency_key": "same-type",
            "content_sha256": "c" * 64,
            "in_reply_to": work_order.work_order_id,
            "source_input_sha256": work_order.content_sha256,
            "created_at": work_order.created_at,
            "packet": {"response_type": "plan.completed"},
            "receipt": {"receipt_type": "response.accepted"},
        }
        with store.lock_work_order(work_order.work_order_id) as session:
            session.put_response_packet(packet)
        with (
            self.assertRaisesRegex(RuntimeError, "Concurrent ledger update"),
            store.lock_work_order(work_order.work_order_id) as session,
        ):
            session._base_sequence = 0
            session.put_response_packet({**packet, "packet_id": "MSG-PLAN-OTHER"})
        with store.lock_work_order(work_order.work_order_id) as session:
            session._base_sequence = 0
            session.put_response_packet(packet)


if __name__ == "__main__":
    unittest.main()
