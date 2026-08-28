from __future__ import annotations

import hashlib
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from pathlib import Path

from ewb.cli import _parser
from ewb.contracts import (
    ExecutorAcknowledgement,
    PlanPacket,
    PlanningDispatch,
    WorkAction,
    WorkKind,
    WorkOrder,
    WorkStatus,
)
from ewb.decorators import parse_directive
from ewb.executors.recording_cursor import RecordingCursorExecutor
from ewb.service import BrokerService
from ewb.storage.sqlite import SQLiteStateStore
from ewb.wrappers import wrap_prompt

FEATURE = """@ewb feature.plan

# Feature
"""
REPOSITORY = "https://github.com/example/project"


class FailOnceExecutor(RecordingCursorExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def submit_for_planning(self, dispatch: PlanningDispatch) -> ExecutorAcknowledgement:
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("simulated dispatch crash")
        return super().submit_for_planning(dispatch)


class ReplayAndPlanCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStateStore(Path(self.temp_dir.name) / "ewb.db")
        self.cursor = RecordingCursorExecutor()
        self.service = BrokerService(self.store, self.cursor)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_replay_resumes_dispatch_after_executor_failure(self) -> None:
        executor = FailOnceExecutor()
        service = BrokerService(self.store, executor)
        with self.assertRaisesRegex(RuntimeError, "simulated dispatch crash"):
            service.submit_prompt_for_planning(
                markdown=FEATURE,
                sender="chatgpt:planner",
                recipient="cursor:backend",
                repository_url=REPOSITORY,
                idempotency_key="resume-dispatch",
            )
        self.assertEqual(executor.attempts, 1)
        self.assertEqual(len(executor.dispatches), 0)
        stored = self.store.get_by_idempotency_key("resume-dispatch")
        assert stored is not None
        self.assertEqual(stored.status, WorkStatus.FAILED)

        receipt = service.submit_prompt_for_planning(
            markdown=FEATURE,
            sender="chatgpt:planner",
            recipient="cursor:backend",
            repository_url=REPOSITORY,
            idempotency_key="resume-dispatch",
        )
        self.assertFalse(receipt.duplicate)
        self.assertEqual(receipt.status, WorkStatus.PLANNING)
        self.assertEqual(receipt.work_order_id, stored.work_order_id)
        self.assertEqual(executor.attempts, 2)
        self.assertEqual(len(executor.dispatches), 1)
        self.assertEqual(executor.dispatches[0].work_order_id, stored.work_order_id)
        self.assertEqual(
            [entry.event_type for entry in self.store.list_ledger(receipt.work_order_id)],
            [
                "work_order.accepted",
                "work_order.routed",
                "executor.failed",
                "executor.acknowledged",
            ],
        )

    def test_replay_resumes_accepted_work_order_before_dispatch(self) -> None:
        work_order_id = "EWB-11111111-1111-1111-1111-111111111111"
        directive = parse_directive(FEATURE)
        wrapped = wrap_prompt(directive, FEATURE, work_order_id)
        content_sha256 = hashlib.sha256(FEATURE.encode("utf-8")).hexdigest()
        candidate = WorkOrder(
            work_order_id=work_order_id,
            idempotency_key="accepted-only",
            sender="chatgpt:planner",
            recipient="cursor:backend",
            kind=WorkKind.FEATURE,
            action=WorkAction.PLAN,
            parent_work_order_id=None,
            repository_url=REPOSITORY,
            base_ref="main",
            markdown=FEATURE,
            content_sha256=content_sha256,
            wrapper_id=wrapped.wrapper_id,
            wrapper_version=wrapped.wrapper_version,
            wrapper_sha256=wrapped.wrapper_sha256,
            status=WorkStatus.ACCEPTED,
            created_at=datetime.now(UTC).isoformat(),
        )
        created, inserted, _sequence = self.store.create_work_order(candidate)
        self.assertTrue(inserted)
        self.assertEqual(created.work_order_id, work_order_id)

        receipt = self.service.submit_prompt_for_planning(
            markdown=FEATURE,
            sender="chatgpt:planner",
            recipient="cursor:backend",
            repository_url=REPOSITORY,
            idempotency_key="accepted-only",
        )
        self.assertFalse(receipt.duplicate)
        self.assertEqual(receipt.work_order_id, work_order_id)
        self.assertEqual(receipt.status, WorkStatus.PLANNING)
        self.assertEqual(len(self.cursor.dispatches), 1)
        self.assertEqual(self.cursor.dispatches[0].work_order_id, work_order_id)
        self.assertIn(work_order_id, self.cursor.dispatches[0].wrapped_markdown)
        self.assertEqual(
            [entry.event_type for entry in self.store.list_ledger(work_order_id)],
            ["work_order.accepted", "work_order.routed", "executor.acknowledged"],
        )

    def test_refresh_completes_plan_available_after_partial_capture(self) -> None:
        receipt = self.service.submit_prompt_for_planning(
            markdown=FEATURE,
            sender="chatgpt:planner",
            recipient="cursor:backend",
            repository_url=REPOSITORY,
            idempotency_key="partial-plan",
        )
        acknowledgement = self.store.list_ledger(receipt.work_order_id)[-1]
        content = self.cursor.plan_result
        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self.store.append_ledger(
            work_order_id=receipt.work_order_id,
            event_type="plan.received",
            actor="cursor:recording",
            counterparty="broker:ewb",
            payload={
                "plan_id": f"PLAN-{content_sha256[:24]}",
                "executor_agent_id": acknowledgement.payload["executor_agent_id"],
                "executor_run_id": acknowledgement.payload["executor_run_id"],
                "content": content,
                "content_sha256": content_sha256,
                "duration_ms": 1,
                "git": None,
            },
        )
        stored = self.store.get_work_order(receipt.work_order_id)
        assert stored is not None
        self.assertEqual(stored.status, WorkStatus.PLANNING)
        self.assertIsNone(
            next(
                (
                    entry
                    for entry in self.store.list_ledger(receipt.work_order_id)
                    if entry.event_type == "plan.available"
                ),
                None,
            )
        )

        packet = self.service.refresh_planning(receipt.work_order_id)
        self.assertEqual(packet.content, content)
        self.assertEqual(
            [entry.event_type for entry in self.store.list_ledger(receipt.work_order_id)],
            [
                "work_order.accepted",
                "work_order.routed",
                "executor.acknowledged",
                "plan.received",
                "plan.available",
            ],
        )
        ready = self.store.get_work_order(receipt.work_order_id)
        assert ready is not None
        self.assertEqual(ready.status, WorkStatus.PLAN_READY)

    def test_get_plan_completes_missing_plan_available(self) -> None:
        receipt = self.service.submit_prompt_for_planning(
            markdown=FEATURE,
            sender="chatgpt:planner",
            recipient="cursor:backend",
            repository_url=REPOSITORY,
            idempotency_key="partial-get-plan",
        )
        acknowledgement = self.store.list_ledger(receipt.work_order_id)[-1]
        content = self.cursor.plan_result
        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self.store.append_ledger(
            work_order_id=receipt.work_order_id,
            event_type="plan.received",
            actor="cursor:recording",
            counterparty="broker:ewb",
            payload={
                "plan_id": f"PLAN-{content_sha256[:24]}",
                "executor_agent_id": acknowledgement.payload["executor_agent_id"],
                "executor_run_id": acknowledgement.payload["executor_run_id"],
                "content": content,
                "content_sha256": content_sha256,
            },
        )

        packet = self.service.get_plan(receipt.work_order_id)
        self.assertEqual(packet.content, content)
        events = [entry.event_type for entry in self.store.list_ledger(receipt.work_order_id)]
        self.assertEqual(events.count("plan.available"), 1)

    def test_concurrent_refresh_records_plan_once(self) -> None:
        receipt = self.service.submit_prompt_for_planning(
            markdown=FEATURE,
            sender="chatgpt:planner",
            recipient="cursor:backend",
            repository_url=REPOSITORY,
            idempotency_key="concurrent-plan",
        )
        results: list[PlanPacket] = []
        errors: list[BaseException] = []
        barrier = threading.Barrier(8)

        def worker() -> None:
            try:
                barrier.wait()
                result = self.service.refresh_planning(receipt.work_order_id)
                if not isinstance(result, PlanPacket):
                    raise AssertionError(f"expected PlanPacket, got {type(result)}")
                results.append(result)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 8)
        first = results[0]
        for result in results[1:]:
            self.assertEqual(result.to_dict(), first.to_dict())
        events = [entry.event_type for entry in self.store.list_ledger(receipt.work_order_id)]
        self.assertEqual(events.count("plan.received"), 1)
        self.assertEqual(events.count("plan.available"), 1)


class CliDefaultTests(unittest.TestCase):
    def test_ledger_defaults_to_same_db_as_submit(self) -> None:
        parser = _parser()
        ledger = parser.parse_args(["ledger"])
        submit = parser.parse_args(["submit", "prompt.md"])
        self.assertEqual(ledger.db, Path(".ewb/ewb.db"))
        self.assertEqual(submit.db, Path(".ewb/ewb.db"))


if __name__ == "__main__":
    unittest.main()
