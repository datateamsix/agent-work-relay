from __future__ import annotations

import hashlib
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from pathlib import Path

from awr.contracts import PlanPacket, WorkAction, WorkKind, WorkOrder, WorkStatus
from awr.decorators import parse_directive
from awr.executors.recording_cursor import RecordingCursorExecutor
from awr.service import BrokerService
from awr.storage.firestore import FirestoreStateStore
from awr.storage.firestore_memory import InMemoryFirestore
from awr.storage.sqlite import SQLiteStateStore
from awr.wrappers import wrap_prompt

FEATURE = """@awr feature.plan

# Feature
"""
REPOSITORY = "https://github.com/example/project"


def _accepted_work_order(work_order_id: str, idempotency_key: str) -> WorkOrder:
    directive = parse_directive(FEATURE)
    wrapped = wrap_prompt(directive, FEATURE, work_order_id)
    return WorkOrder(
        work_order_id=work_order_id,
        idempotency_key=idempotency_key,
        sender="chatgpt:planner",
        recipient="cursor:backend",
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


class StorageConformanceMixin:
    store: object

    def _service(self, executor: RecordingCursorExecutor | None = None) -> BrokerService:
        return BrokerService(self.store, executor or RecordingCursorExecutor())  # type: ignore[arg-type]

    def test_create_is_idempotent_and_records_acceptance(self) -> None:
        candidate = _accepted_work_order("AWR-11111111-1111-1111-1111-111111111111", "idem-1")
        first, created, sequence = self.store.create_work_order(candidate)  # type: ignore[attr-defined]
        self.assertTrue(created)
        self.assertEqual(sequence, 1)
        replay, created_again, replay_sequence = self.store.create_work_order(candidate)  # type: ignore[attr-defined]
        self.assertFalse(created_again)
        self.assertEqual(replay.work_order_id, first.work_order_id)
        self.assertEqual(replay_sequence, 1)
        events = [entry.event_type for entry in self.store.list_ledger(first.work_order_id)]  # type: ignore[attr-defined]
        self.assertEqual(events, ["work_order.accepted"])

    def test_golden_path_and_replay(self) -> None:
        cursor = RecordingCursorExecutor()
        service = self._service(cursor)
        first = service.submit_prompt_for_planning(
            markdown=FEATURE,
            sender="chatgpt:planner",
            recipient="cursor:backend",
            repository_url=REPOSITORY,
            idempotency_key="conformance-golden",
        )
        self.assertEqual(first.status, WorkStatus.PLANNING)
        replay = service.submit_prompt_for_planning(
            markdown=FEATURE,
            sender="chatgpt:planner",
            recipient="cursor:backend",
            repository_url=REPOSITORY,
            idempotency_key="conformance-golden",
        )
        self.assertTrue(replay.duplicate)
        self.assertEqual(replay.work_order_id, first.work_order_id)
        self.assertEqual(len(cursor.dispatches), 1)
        packet = service.refresh_planning(first.work_order_id)
        self.assertIsInstance(packet, PlanPacket)
        self.assertEqual(
            [entry.event_type for entry in self.store.list_ledger(first.work_order_id)],  # type: ignore[attr-defined]
            [
                "work_order.accepted",
                "work_order.routed",
                "executor.acknowledged",
                "plan.received",
                "plan.available",
            ],
        )
        again = service.refresh_planning(first.work_order_id)
        self.assertEqual(again.to_dict(), packet.to_dict())

    def test_concurrent_plan_capture_is_once(self) -> None:
        service = self._service()
        receipt = service.submit_prompt_for_planning(
            markdown=FEATURE,
            sender="chatgpt:planner",
            recipient="cursor:backend",
            repository_url=REPOSITORY,
            idempotency_key="conformance-concurrent",
        )
        results: list[PlanPacket] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(8)

        def worker() -> None:
            try:
                barrier.wait()
                result = service.refresh_planning(receipt.work_order_id)
                if not isinstance(result, PlanPacket):
                    raise TypeError(type(result))
                results.append(result)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        events = [entry.event_type for entry in self.store.list_ledger(receipt.work_order_id)]  # type: ignore[attr-defined]
        self.assertEqual(events.count("plan.received"), 1)
        self.assertEqual(events.count("plan.available"), 1)

    def test_lifecycle_statuses_and_atomic_projection(self) -> None:
        from awr.lifecycle.kernel import derive_snapshot

        candidate = _accepted_work_order("AWR-22222222-2222-2222-2222-222222222222", "idem-life")
        work_order, created, _ = self.store.create_work_order(candidate)  # type: ignore[attr-defined]
        self.assertTrue(created)
        snapshot = derive_snapshot(
            work_order.work_order_id,
            work_order.sender,
            work_order.recipient,
            work_order.content_sha256,
        )
        with self.store.lock_work_order(work_order.work_order_id) as session:  # type: ignore[attr-defined]
            session.put_response_packet(
                {
                    "packet_id": "MSG-plan",
                    "response_type": "plan.completed",
                    "actor": work_order.recipient,
                    "idempotency_key": "life-plan",
                    "content_sha256": "c" * 64,
                    "in_reply_to": work_order.work_order_id,
                    "source_input_sha256": work_order.content_sha256,
                    "created_at": work_order.created_at,
                    "packet": {"response_type": "plan.completed"},
                }
            )
            session.put_decision(
                {
                    "decision_id": "DEC-life",
                    "decision_type": "approve_plan",
                    "work_order_id": work_order.work_order_id,
                    "actor": work_order.sender,
                    "target_kind": "plan",
                    "target_id": "PLAN-1",
                    "target_sha256": "c" * 64,
                    "permitted_action": "plan.execute",
                    "scope": "restricted",
                    "created_at": work_order.created_at,
                    "idempotency_key": "life-approve",
                }
            )
            session.update_status(WorkStatus.EXECUTION_DISPATCHED)
            session.append_ledger(
                "plan.execute",
                work_order.sender,
                "broker:awr",
                {"message_id": "EXD-1"},
            )
            session.put_lifecycle(
                {
                    **snapshot.to_dict(),
                    "plan_id": "PLAN-1",
                    "plan_sha256": "c" * 64,
                }
            )
        stored = self.store.get_work_order(work_order.work_order_id)  # type: ignore[attr-defined]
        self.assertEqual(stored.status, WorkStatus.EXECUTION_DISPATCHED)
        with self.store.lock_work_order(work_order.work_order_id) as session:  # type: ignore[attr-defined]
            replay = session.get_response_by_idempotency(
                work_order.recipient, "plan.completed", "life-plan"
            )
            decisions = session.list_decisions()
            lifecycle = session.get_lifecycle()
        self.assertIsNotNone(replay)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["target_id"], "PLAN-1")
        assert lifecycle is not None
        self.assertEqual(lifecycle["plan_id"], "PLAN-1")
        events = [entry.event_type for entry in self.store.list_ledger(work_order.work_order_id)]  # type: ignore[attr-defined]
        self.assertIn("plan.execute", events)

    def test_failed_lifecycle_write_does_not_partially_commit(self) -> None:
        candidate = _accepted_work_order("AWR-33333333-3333-3333-3333-333333333333", "idem-atomic")
        work_order, _, _ = self.store.create_work_order(candidate)  # type: ignore[attr-defined]
        try:
            with self.store.lock_work_order(work_order.work_order_id) as session:  # type: ignore[attr-defined]
                session.update_status(WorkStatus.EXECUTING)
                session.append_ledger(
                    "execution.acknowledged",
                    work_order.recipient,
                    "broker:awr",
                    {"executor_run_id": "run-1"},
                )
                session.put_lifecycle({"work_order_id": work_order.work_order_id})
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        stored = self.store.get_work_order(work_order.work_order_id)  # type: ignore[attr-defined]
        self.assertEqual(stored.status, WorkStatus.ACCEPTED)
        self.assertEqual(
            [entry.event_type for entry in self.store.list_ledger(work_order.work_order_id)],  # type: ignore[attr-defined]
            ["work_order.accepted"],
        )
        with self.store.lock_work_order(work_order.work_order_id) as session:  # type: ignore[attr-defined]
            self.assertIsNone(session.get_lifecycle())

    def test_execution_dispatch_write_rollback_and_cas(self) -> None:
        candidate = _accepted_work_order(
            "AWR-44444444-4444-4444-4444-444444444444", "idem-dispatch"
        )
        work_order, _, _ = self.store.create_work_order(candidate)  # type: ignore[attr-defined]
        now = datetime.now(UTC).isoformat()
        dispatch = {
            "dispatch_id": "EXD-conformance-1",
            "work_order_id": work_order.work_order_id,
            "attempt": 1,
            "plan_id": "PLAN-1",
            "plan_sha256": "c" * 64,
            "approval_decision_id": "DEC-1",
            "executor": "cursor:recording",
            "repository_url": REPOSITORY,
            "base_ref": "main",
            "wrapper_id": "plan.execute",
            "wrapper_version": "1.0.0",
            "wrapper_sha256": "d" * 64,
            "provider_idempotency_key": "provider-key-1",
            "state": "PENDING",
            "attempt_count": 0,
            "wrapped_markdown": "# Execute",
            "created_at": now,
            "updated_at": now,
        }
        try:
            with self.store.lock_work_order(work_order.work_order_id) as session:  # type: ignore[attr-defined]
                session.put_execution_dispatch(dispatch)
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        self.assertIsNone(self.store.get_execution_dispatch("EXD-conformance-1"))  # type: ignore[attr-defined]

        with self.store.lock_work_order(work_order.work_order_id) as session:  # type: ignore[attr-defined]
            session.put_execution_dispatch(dispatch)
            session.append_ledger("plan.execute", work_order.sender, "broker:awr", {"attempt": 1})
            session.append_ledger("plan.execute", work_order.sender, "broker:awr", {"attempt": 2})
            session.append_ledger(
                "execution.progress", work_order.recipient, "broker:awr", {"n": 1}
            )
            session.append_ledger(
                "execution.progress", work_order.recipient, "broker:awr", {"n": 2}
            )
        stored = self.store.get_execution_dispatch("EXD-conformance-1")  # type: ignore[attr-defined]
        assert stored is not None
        events = [entry.event_type for entry in self.store.list_ledger(work_order.work_order_id)]  # type: ignore[attr-defined]
        self.assertEqual(events.count("plan.execute"), 2)
        self.assertEqual(events.count("execution.progress"), 2)

        first = self.store.claim_execution_lease(  # type: ignore[attr-defined]
            "EXD-conformance-1", owner="worker-a", now=now, ttl_seconds=30
        )
        second = self.store.claim_execution_lease(  # type: ignore[attr-defined]
            "EXD-conformance-1",
            owner="worker-b",
            now=now,
            ttl_seconds=30,
        )
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        expired = self.store.claim_execution_lease(  # type: ignore[attr-defined]
            "EXD-conformance-1",
            owner="worker-b",
            now=(datetime.now(UTC).replace(year=2099)).isoformat(),
            ttl_seconds=30,
        )
        self.assertIsNotNone(expired)
        self.assertEqual(expired["lease_owner"], "worker-b")


class SQLiteConformanceTests(StorageConformanceMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStateStore(Path(self.temp_dir.name) / "awr.db")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()


class FirestoreMemoryConformanceTests(StorageConformanceMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.store = FirestoreStateStore(InMemoryFirestore())


if __name__ == "__main__":
    unittest.main()
