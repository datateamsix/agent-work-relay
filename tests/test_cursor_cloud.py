from __future__ import annotations

import os
import unittest
from typing import Any

from awr.contracts import ExecutorRunStatus, PlanningDispatch
from awr.executors.cursor_cloud import CursorAPIError, CursorCloudExecutor
from awr.executors.execution import AmbiguousAcceptance, ExecutionDispatch


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
        self.headers: list[dict[str, str]] = []
        self.responses: list[FakeResponse] = []
        self.raise_timeout = False

    def post(
        self, path: str, *, json: dict[str, Any], headers: dict[str, str] | None = None
    ) -> FakeResponse:
        if self.raise_timeout:
            raise TimeoutError("Cursor request timed out.")
        self.posts.append((path, json))
        self.headers.append(headers or {})
        return self.responses.pop(0)

    def get(self, path: str) -> FakeResponse:
        self.gets.append(path)
        return self.responses.pop(0)


def dispatch(existing_agent_id: str | None = None) -> PlanningDispatch:
    return PlanningDispatch(
        work_order_id="AWR-00000000-0000-0000-0000-000000000001",
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


def execution_dispatch(existing_agent_id: str | None = None) -> ExecutionDispatch:
    return ExecutionDispatch(
        dispatch_id="EXD-1",
        work_order_id="AWR-00000000-0000-0000-0000-000000000001",
        attempt=1,
        plan_id="PLAN-1",
        plan_sha256="a" * 64,
        approval_decision_id="DEC-1",
        executor="cursor:cloud",
        repository_url="https://github.com/example/project",
        base_ref="main",
        wrapper_id="plan.execute",
        wrapper_version="1.0.0",
        wrapper_sha256="b" * 64,
        provider_idempotency_key="idem-exec-1",
        wrapped_markdown="# Execute the approved plan",
        existing_agent_id=existing_agent_id,
    )


class CursorCloudExecutionTests(unittest.TestCase):
    def test_create_agent_uses_agent_mode_and_safety_controls(self) -> None:
        client = FakeClient()
        client.responses.append(
            FakeResponse(
                201,
                {
                    "agent": {"id": "bc-exec-1", "url": "https://cursor.com/agents/bc-exec-1"},
                    "run": {"id": "xrun-1"},
                },
            )
        )
        executor = CursorCloudExecutor("test-key", client=client)

        acknowledgement = executor.submit_for_execution(execution_dispatch())

        path, payload = client.posts[0]
        self.assertEqual(path, "/v1/agents")
        self.assertEqual(payload["mode"], "agent")
        self.assertFalse(payload["workOnCurrentBranch"])
        self.assertFalse(payload["autoCreatePR"])
        self.assertEqual(
            payload["repos"],
            [{"url": "https://github.com/example/project", "startingRef": "main"}],
        )
        self.assertEqual(client.headers[0]["Idempotency-Key"], "idem-exec-1")
        self.assertEqual(acknowledgement.executor_run_id, "xrun-1")
        self.assertNotIn("test-key", str(acknowledgement))

    def test_follow_up_reuses_planning_agent_in_agent_mode(self) -> None:
        client = FakeClient()
        client.responses.append(FakeResponse(201, {"run": {"id": "xrun-2"}}))
        executor = CursorCloudExecutor("test-key", client=client)

        acknowledgement = executor.submit_for_execution(execution_dispatch("bc-plan"))

        path, payload = client.posts[0]
        self.assertEqual(path, "/v1/agents/bc-plan/runs")
        self.assertEqual(payload["mode"], "agent")
        self.assertEqual(acknowledgement.executor_agent_id, "bc-plan")

    def test_follow_up_timeout_is_reconciliation_required(self) -> None:
        client = FakeClient()
        client.raise_timeout = True
        executor = CursorCloudExecutor("test-key", client=client)
        with self.assertRaises(AmbiguousAcceptance) as caught:
            executor.submit_for_execution(execution_dispatch("bc-plan"))
        self.assertEqual(caught.exception.code, "RECONCILIATION_REQUIRED")
        self.assertNotIn("test-key", str(caught.exception))

    def test_follow_up_conflict_fails_closed(self) -> None:
        client = FakeClient()
        client.responses.append(FakeResponse(409, {"code": "agent_busy"}))
        executor = CursorCloudExecutor("test-key", client=client)
        with self.assertRaises(AmbiguousAcceptance):
            executor.submit_for_execution(execution_dispatch("bc-plan"))

    def test_create_conflict_recovers_existing_run(self) -> None:
        client = FakeClient()
        client.responses.extend(
            [
                FakeResponse(409, {"code": "agent_id_conflict"}),
                FakeResponse(200, {"id": "bc-recovered", "latestRunId": "xrun-existing"}),
            ]
        )
        executor = CursorCloudExecutor("test-key", client=client)
        acknowledgement = executor.submit_for_execution(execution_dispatch())
        self.assertEqual(acknowledgement.executor_run_id, "xrun-existing")
        self.assertIn("Recovered", acknowledgement.message)

    def test_rate_limit_fails_closed(self) -> None:
        client = FakeClient()
        client.responses.append(FakeResponse(429, {"error": "rate_limited"}))
        executor = CursorCloudExecutor("test-key", client=client)
        with self.assertRaises(CursorAPIError) as caught:
            executor.submit_for_execution(execution_dispatch())
        self.assertNotIn("test-key", str(caught.exception))

    def test_unknown_run_status_fails_closed(self) -> None:
        client = FakeClient()
        client.responses.append(FakeResponse(200, {"status": "WEIRD", "id": "xrun-1"}))
        executor = CursorCloudExecutor("test-key", client=client)
        with self.assertRaises(CursorAPIError):
            executor.get_execution_run("bc-1", "xrun-1")

    def test_malformed_provider_response_fails_closed(self) -> None:
        client = FakeClient()
        client.responses.append(FakeResponse(201, {"agent": {"id": "bc-1"}}))
        executor = CursorCloudExecutor("test-key", client=client)
        with self.assertRaises(CursorAPIError):
            executor.submit_for_execution(execution_dispatch())

    def test_create_timeout_without_recovery_is_reconciliation_required(self) -> None:
        client = FakeClient()
        client.raise_timeout = True
        executor = CursorCloudExecutor("test-key", client=client)
        with self.assertRaises(AmbiguousAcceptance):
            executor.submit_for_execution(execution_dispatch())

    @unittest.skipUnless(
        os.getenv("AWR_LIVE_CURSOR_EXECUTION") == "1" and os.getenv("CURSOR_API_KEY"),
        "live Cursor credentials were not authorized",
    )
    def test_live_execution_opt_in(self) -> None:  # pragma: no cover
        self.skipTest("Live Cursor execution proof is not fabricated in this slice.")


if __name__ == "__main__":
    unittest.main()
