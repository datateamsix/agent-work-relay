from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from ewb.contracts import WorkStatus
from ewb.executors.recording_cursor import RecordingCursorExecutor
from ewb.service import BrokerService
from ewb.storage.sqlite import SQLiteStateStore

PROMPT = """@ewb feature.plan

# Add project health endpoint

Produce an implementation plan. Do not edit files.
"""


class GoldenPromptToPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "ewb.db"
        self.store = SQLiteStateStore(self.db_path)
        self.cursor = RecordingCursorExecutor()
        self.service = BrokerService(self.store, self.cursor)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_prompt_is_wrapped_routed_receipted_and_idempotent(self) -> None:
        first = self.service.submit_prompt_for_planning(
            markdown=PROMPT,
            sender="chatgpt:planner",
            recipient="cursor:backend",
            idempotency_key="golden-001",
        )

        self.assertFalse(first.duplicate)
        self.assertEqual(first.status, WorkStatus.PLANNING)
        self.assertEqual(first.content_sha256, hashlib.sha256(PROMPT.encode()).hexdigest())
        self.assertEqual(len(self.cursor.dispatches), 1)
        dispatch = self.cursor.dispatches[0]
        self.assertEqual(dispatch.mode, "PLAN_ONLY")
        self.assertEqual(dispatch.wrapper_id, "feature.plan")
        self.assertEqual(dispatch.wrapper_version, "1.0.0")
        self.assertIn("Do not edit files", dispatch.wrapped_markdown)

        ledger = self.store.list_ledger(first.work_order_id)
        self.assertEqual(
            [entry.event_type for entry in ledger],
            ["work_order.accepted", "work_order.routed", "executor.acknowledged"],
        )
        self.assertEqual([entry.sequence for entry in ledger], sorted(e.sequence for e in ledger))

        replay = self.service.submit_prompt_for_planning(
            markdown=PROMPT,
            sender="chatgpt:planner",
            recipient="cursor:backend",
            idempotency_key="golden-001",
        )
        self.assertTrue(replay.duplicate)
        self.assertEqual(replay.work_order_id, first.work_order_id)
        self.assertEqual(replay.status, WorkStatus.PLANNING)
        self.assertEqual(replay.executor_run_id, first.executor_run_id)
        self.assertEqual(len(self.cursor.dispatches), 1)
        self.assertEqual(len(self.store.list_ledger(first.work_order_id)), 3)


if __name__ == "__main__":
    unittest.main()
