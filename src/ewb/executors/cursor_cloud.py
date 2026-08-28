from __future__ import annotations

from typing import Any
from uuid import UUID

from ..contracts import (
    ExecutorAcknowledgement,
    ExecutorRunStatus,
    PlanningDispatch,
    PlanningRunResult,
)


class CursorAPIError(RuntimeError):
    """Cursor rejected a broker operation or returned an invalid response."""


class CursorCloudExecutor:
    """Cursor Cloud Agents API v1 adapter for plan-only runs."""

    name = "cursor:cloud"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.cursor.com",
        timeout_seconds: float = 30.0,
        client: Any | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Cursor API key is required.")
        self.base_url = base_url.rstrip("/")
        if client is None:
            try:
                import httpx
            except ImportError as exc:
                raise RuntimeError(
                    "Install the Cursor adapter with: uv sync --extra cursor"
                ) from exc
            client = httpx.Client(
                base_url=self.base_url,
                auth=(api_key, ""),
                headers={"Accept": "application/json"},
                timeout=timeout_seconds,
            )
        self._client = client

    def submit_for_planning(self, dispatch: PlanningDispatch) -> ExecutorAcknowledgement:
        if dispatch.mode != "PLAN_ONLY":
            raise ValueError("CursorCloudExecutor accepts PLAN_ONLY packets only.")

        if dispatch.existing_agent_id:
            response = self._client.post(
                f"/v1/agents/{dispatch.existing_agent_id}/runs",
                json={"prompt": {"text": dispatch.wrapped_markdown}, "mode": "plan"},
            )
            payload = self._response_json(response, expected={200, 201, 202})
            run = self._object(payload, "run")
            return ExecutorAcknowledgement(
                executor_agent_id=dispatch.existing_agent_id,
                executor_run_id=self._string(run, "id"),
                executor=self.name,
                executor_url=None,
                accepted=True,
                message="Cursor follow-up planning run accepted.",
            )

        agent_id = self._agent_id_for_work_order(dispatch.work_order_id)
        response = self._client.post(
            "/v1/agents",
            json={
                "agentId": agent_id,
                "name": f"EWB planning {dispatch.work_order_id}",
                "prompt": {"text": dispatch.wrapped_markdown},
                "repos": [{"url": dispatch.repository_url, "startingRef": dispatch.base_ref}],
                "mode": "plan",
                "workOnCurrentBranch": False,
                "autoCreatePR": False,
            },
        )
        if response.status_code == 409:
            return self._recover_idempotent_create(agent_id)

        payload = self._response_json(response, expected={200, 201, 202})
        agent = self._object(payload, "agent")
        run = self._object(payload, "run")
        return ExecutorAcknowledgement(
            executor_agent_id=self._string(agent, "id"),
            executor_run_id=self._string(run, "id"),
            executor=self.name,
            executor_url=self._optional_string(agent, "url"),
            accepted=True,
            message="Cursor planning run accepted.",
        )

    def get_planning_run(self, executor_agent_id: str, executor_run_id: str) -> PlanningRunResult:
        response = self._client.get(f"/v1/agents/{executor_agent_id}/runs/{executor_run_id}")
        payload = self._response_json(response, expected={200})
        try:
            status = ExecutorRunStatus(self._string(payload, "status"))
        except ValueError as exc:
            raise CursorAPIError(
                f"Cursor returned an unknown run status: {payload.get('status')!r}"
            ) from exc
        duration = payload.get("durationMs")
        return PlanningRunResult(
            executor_agent_id=executor_agent_id,
            executor_run_id=executor_run_id,
            executor=self.name,
            status=status,
            result=self._optional_string(payload, "result"),
            duration_ms=duration if isinstance(duration, int) else None,
            git=payload.get("git") if isinstance(payload.get("git"), dict) else None,
        )

    def _recover_idempotent_create(self, agent_id: str) -> ExecutorAcknowledgement:
        response = self._client.get(f"/v1/agents/{agent_id}")
        agent = self._response_json(response, expected={200})
        return ExecutorAcknowledgement(
            executor_agent_id=self._string(agent, "id"),
            executor_run_id=self._string(agent, "latestRunId"),
            executor=self.name,
            executor_url=self._optional_string(agent, "url"),
            accepted=True,
            message="Recovered the existing idempotent Cursor planning run.",
        )

    @staticmethod
    def _agent_id_for_work_order(work_order_id: str) -> str:
        prefix = "EWB-"
        if not work_order_id.startswith(prefix):
            raise ValueError(f"Invalid EWB work-order ID: {work_order_id}")
        value = UUID(work_order_id.removeprefix(prefix))
        return f"bc-{value}"

    @staticmethod
    def _response_json(response: Any, *, expected: set[int]) -> dict[str, Any]:
        if response.status_code not in expected:
            detail = str(getattr(response, "text", ""))[:500]
            raise CursorAPIError(
                f"Cursor API returned HTTP {response.status_code}: {detail or 'no response body'}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise CursorAPIError("Cursor API returned a non-object JSON response.")
        return payload

    @staticmethod
    def _object(payload: dict[str, Any], key: str) -> dict[str, Any]:
        value = payload.get(key)
        if not isinstance(value, dict):
            raise CursorAPIError(f"Cursor response is missing object field {key!r}.")
        return value

    @staticmethod
    def _string(payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise CursorAPIError(f"Cursor response is missing string field {key!r}.")
        return value

    @staticmethod
    def _optional_string(payload: dict[str, Any], key: str) -> str | None:
        value = payload.get(key)
        return value if isinstance(value, str) and value else None
