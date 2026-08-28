from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ewb.executors.recording_cursor import RecordingCursorExecutor
from ewb.service import BrokerService, WorkOrderValidationError
from ewb.storage.sqlite import SQLiteStateStore

FEATURE = """@ewb feature.plan

# Feature
"""
REPOSITORY = "https://github.com/example/project"


class ServiceValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cursor = RecordingCursorExecutor()
        self.service = BrokerService(
            SQLiteStateStore(Path(self.temp_dir.name) / "ewb.db"), self.cursor
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_feature_requires_repository(self) -> None:
        with self.assertRaisesRegex(WorkOrderValidationError, "repository_url"):
            self.service.submit_prompt_for_planning(
                markdown=FEATURE,
                sender="chatgpt:planner",
                recipient="cursor:backend",
            )

    def test_unknown_refinement_parent_fails_closed(self) -> None:
        with self.assertRaisesRegex(WorkOrderValidationError, "Unknown parent"):
            self.service.submit_prompt_for_planning(
                markdown="@ewb refinement.plan parent=EWB-missing\n\nRefine it.",
                sender="chatgpt:planner",
                recipient="cursor:backend",
            )

    def test_refinement_reuses_parent_executor_session(self) -> None:
        parent = self.service.submit_prompt_for_planning(
            markdown=FEATURE,
            sender="chatgpt:planner",
            recipient="cursor:backend",
            repository_url=REPOSITORY,
        )
        parent_agent_id = self.cursor.dispatches[0].existing_agent_id
        self.assertIsNone(parent_agent_id)

        self.service.submit_prompt_for_planning(
            markdown=f"@ewb refinement.plan parent={parent.work_order_id}\n\nTighten the plan.",
            sender="chatgpt:planner",
            recipient="cursor:backend",
        )

        acknowledgement = self.service.get_work_order_timeline(parent.work_order_id)[-1]
        expected_agent_id = acknowledgement["payload"]["executor_agent_id"]
        self.assertEqual(self.cursor.dispatches[1].existing_agent_id, expected_agent_id)

    def test_idempotency_key_cannot_be_rebound(self) -> None:
        self.service.submit_prompt_for_planning(
            markdown=FEATURE,
            sender="chatgpt:planner",
            recipient="cursor:backend",
            repository_url=REPOSITORY,
            idempotency_key="same-key",
        )

        with self.assertRaisesRegex(WorkOrderValidationError, "different work order"):
            self.service.submit_prompt_for_planning(
                markdown=FEATURE + "\nDifferent payload.",
                sender="chatgpt:planner",
                recipient="cursor:backend",
                repository_url=REPOSITORY,
                idempotency_key="same-key",
            )


if __name__ == "__main__":
    unittest.main()
