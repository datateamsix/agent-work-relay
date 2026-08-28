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
