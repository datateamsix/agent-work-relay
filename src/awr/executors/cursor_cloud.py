from __future__ import annotations

from typing import Any
from uuid import UUID, uuid5

_TIMEOUT_ERRORS: tuple[type[BaseException], ...]
try:
    from httpx import TimeoutException

    _TIMEOUT_ERRORS = (TimeoutError, TimeoutException)
except ImportError:  # pragma: no cover - optional cursor extra
    _TIMEOUT_ERRORS = (TimeoutError,)

from ..contracts import (
    ExecutorAcknowledgement,
    ExecutorRunStatus,
    PlanningDispatch,
    PlanningRunResult,
)
from .execution import (
    AmbiguousAcceptance,
    ExecutionAcknowledgement,
    ExecutionCapabilities,
    ExecutionDispatch,
    ExecutionRunResult,
)

_EXECUTION_NAMESPACE = UUID("8f3c1e2a-4b5d-6e7f-8091-a2b3c4d5e6f7")


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
                "name": f"AWR planning {dispatch.work_order_id}",
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
        prefix = "AWR-"
        if not work_order_id.startswith(prefix):
            raise ValueError(f"Invalid AWR work-order ID: {work_order_id}")
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

    def submit_for_execution(self, dispatch: ExecutionDispatch) -> ExecutionAcknowledgement:
        headers = {"Idempotency-Key": dispatch.provider_idempotency_key}
        if dispatch.existing_agent_id:
            try:
                response = self._client.post(
                    f"/v1/agents/{dispatch.existing_agent_id}/runs",
                    json={"prompt": {"text": dispatch.wrapped_markdown}, "mode": "agent"},
                    headers=headers,
                )
            except _TIMEOUT_ERRORS as exc:
                raise AmbiguousAcceptance(
                    "Follow-up execution timed out before Cursor acceptance was proven."
                ) from exc
            if getattr(response, "status_code", 0) == 429:
                raise CursorAPIError("Cursor rate-limited the execution follow-up.")
            if getattr(response, "status_code", 0) == 409:
                recovered = self.recover_execution_submission(dispatch)
                if recovered is None:
                    raise AmbiguousAcceptance(
                        "Cursor reported agent_busy without a recoverable run."
                    )
                return recovered
            payload = self._response_json(response, expected={200, 201, 202})
            run = self._object(payload, "run")
            return ExecutionAcknowledgement(
                executor_agent_id=dispatch.existing_agent_id,
                executor_run_id=self._string(run, "id"),
                executor=self.name,
                accepted=True,
                message="Cursor follow-up execution run accepted.",
            )

        agent_id = self._agent_id_for_dispatch(dispatch.provider_idempotency_key)
        try:
            response = self._client.post(
                "/v1/agents",
                json={
                    "agentId": agent_id,
                    "name": f"AWR execution {dispatch.work_order_id} attempt {dispatch.attempt}",
                    "prompt": {"text": dispatch.wrapped_markdown},
                    "repos": [{"url": dispatch.repository_url, "startingRef": dispatch.base_ref}],
                    "mode": "agent",
                    "workOnCurrentBranch": False,
                    "autoCreatePR": False,
                },
                headers=headers,
            )
        except _TIMEOUT_ERRORS as exc:
            recovered = self.recover_execution_submission(dispatch)
            if recovered is None:
                raise AmbiguousAcceptance(
                    "Execution create timed out before Cursor acceptance was proven."
                ) from exc
            return recovered
        if response.status_code == 429:
            raise CursorAPIError("Cursor rate-limited the execution create.")
        if response.status_code == 409:
            recovered = self.recover_execution_submission(dispatch)
            if recovered is None:
                raise AmbiguousAcceptance("Cursor agent_id_conflict without a recoverable run.")
            return recovered
        payload = self._response_json(response, expected={200, 201, 202})
        agent = self._object(payload, "agent")
        run = self._object(payload, "run")
        return ExecutionAcknowledgement(
            executor_agent_id=self._string(agent, "id"),
            executor_run_id=self._string(run, "id"),
            executor=self.name,
            executor_url=self._optional_string(agent, "url"),
            accepted=True,
            message="Cursor execution run accepted.",
        )

    def recover_execution_submission(
        self, dispatch: ExecutionDispatch
    ) -> ExecutionAcknowledgement | None:
        agent_id = dispatch.existing_agent_id or self._agent_id_for_dispatch(
            dispatch.provider_idempotency_key
        )
        try:
            response = self._client.get(f"/v1/agents/{agent_id}")
            agent = self._response_json(response, expected={200})
        except Exception:  # noqa: BLE001
            return None
        run_id = self._optional_string(agent, "latestRunId")
        if not run_id:
            return None
        if dispatch.existing_agent_id:
            return None
        return ExecutionAcknowledgement(
            executor_agent_id=self._string(agent, "id"),
            executor_run_id=run_id,
            executor=self.name,
            executor_url=self._optional_string(agent, "url"),
            accepted=True,
            message="Recovered the existing idempotent Cursor execution run.",
        )

    def get_execution_run(self, executor_agent_id: str, executor_run_id: str) -> ExecutionRunResult:
        response = self._client.get(f"/v1/agents/{executor_agent_id}/runs/{executor_run_id}")
        payload = self._response_json(response, expected={200})
        try:
            status = ExecutorRunStatus(self._string(payload, "status"))
        except ValueError as exc:
            raise CursorAPIError(
                f"Cursor returned an unknown run status: {payload.get('status')!r}"
            ) from exc
        duration = payload.get("durationMs")
        return ExecutionRunResult(
            executor_agent_id=executor_agent_id,
            executor_run_id=executor_run_id,
            executor=self.name,
            status=status,
            result=self._optional_string(payload, "result"),
            duration_ms=duration if isinstance(duration, int) else None,
            git=payload.get("git") if isinstance(payload.get("git"), dict) else None,
        )

    def capabilities(self) -> ExecutionCapabilities:
        return ExecutionCapabilities(
            follow_up_reuse=True,
            client_supplied_agent_id=True,
            run_idempotency_header=True,
            list_runs=True,
        )

    @staticmethod
    def _agent_id_for_dispatch(provider_idempotency_key: str) -> str:
        return f"bc-{uuid5(_EXECUTION_NAMESPACE, provider_idempotency_key)}"
