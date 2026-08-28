from __future__ import annotations

import unittest
from typing import Any

from ewb.contracts import ExecutorRunStatus, PlanningDispatch
from ewb.executors.cursor_cloud import CursorCloudExecutor


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeClient:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.gets: list[str] = []
        self.responses: list[FakeResponse] = []

    def post(self, path: str, *, json: dict[str, Any]) -> FakeResponse:
        self.posts.append((path, json))
        return self.responses.pop(0)

    def get(self, path: str) -> FakeResponse:
        self.gets.append(path)
        return self.responses.pop(0)


def dispatch(existing_agent_id: str | None = None) -> PlanningDispatch:
    return PlanningDispatch(
        work_order_id="EWB-00000000-0000-0000-0000-000000000001",
        recipient="cursor:backend",
        mode="PLAN_ONLY",
        repository_url="https://github.com/example/project",
        base_ref="main",
        existing_agent_id=existing_agent_id,
        wrapped_markdown="# Plan only",
        content_sha256="content-hash",
        wrapper_id="feature.plan",
        wrapper_version="1.0.0",
        wrapper_sha256="wrapper-hash",
    )


class CursorCloudExecutorTests(unittest.TestCase):
    def test_create_agent_uses_plan_only_safety_controls(self) -> None:
        client = FakeClient()
        client.responses.append(
            FakeResponse(
                201,
                {
                    "agent": {
                        "id": "bc-00000000-0000-0000-0000-000000000001",
                        "url": "https://cursor.com/agents/bc-1",
                    },
                    "run": {"id": "run-1"},
                },
            )
        )
        executor = CursorCloudExecutor("test-key", client=client)

        acknowledgement = executor.submit_for_planning(dispatch())

        path, payload = client.posts[0]
        self.assertEqual(path, "/v1/agents")
        self.assertEqual(payload["mode"], "plan")
        self.assertFalse(payload["workOnCurrentBranch"])
        self.assertFalse(payload["autoCreatePR"])
        self.assertEqual(payload["agentId"], "bc-00000000-0000-0000-0000-000000000001")
        self.assertEqual(
            payload["repos"],
            [{"url": "https://github.com/example/project", "startingRef": "main"}],
        )
        self.assertEqual(acknowledgement.executor_run_id, "run-1")

    def test_follow_up_reuses_durable_agent_in_plan_mode(self) -> None:
        client = FakeClient()
        client.responses.append(FakeResponse(201, {"run": {"id": "run-2"}}))
        executor = CursorCloudExecutor("test-key", client=client)

        acknowledgement = executor.submit_for_planning(dispatch("bc-existing"))

        path, payload = client.posts[0]
        self.assertEqual(path, "/v1/agents/bc-existing/runs")
        self.assertEqual(payload["mode"], "plan")
        self.assertEqual(acknowledgement.executor_agent_id, "bc-existing")
        self.assertEqual(acknowledgement.executor_run_id, "run-2")

    def test_duplicate_agent_create_recovers_existing_run(self) -> None:
        client = FakeClient()
        client.responses.extend(
            [
                FakeResponse(409, {"code": "agent_id_conflict"}),
                FakeResponse(
                    200,
                    {
                        "id": "bc-00000000-0000-0000-0000-000000000001",
                        "latestRunId": "run-existing",
                        "url": "https://cursor.com/agents/bc-existing",
                    },
                ),
            ]
        )
        executor = CursorCloudExecutor("test-key", client=client)

        acknowledgement = executor.submit_for_planning(dispatch())

        self.assertEqual(
            client.gets,
            ["/v1/agents/bc-00000000-0000-0000-0000-000000000001"],
        )
        self.assertEqual(acknowledgement.executor_run_id, "run-existing")
        self.assertIn("Recovered", acknowledgement.message)

    def test_get_finished_run_returns_plan_text(self) -> None:
        client = FakeClient()
        client.responses.append(
            FakeResponse(
                200,
                {
                    "id": "run-1",
                    "agentId": "bc-1",
                    "status": "FINISHED",
                    "result": "# Implementation plan",
                    "durationMs": 1234,
                },
            )
        )
        executor = CursorCloudExecutor("test-key", client=client)

        result = executor.get_planning_run("bc-1", "run-1")

        self.assertEqual(result.status, ExecutorRunStatus.FINISHED)
        self.assertEqual(result.result, "# Implementation plan")
        self.assertEqual(result.duration_ms, 1234)


if __name__ == "__main__":
    unittest.main()
