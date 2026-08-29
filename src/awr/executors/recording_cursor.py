from __future__ import annotations

from uuid import uuid4

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


class RecordingCursorExecutor:
    """Credential-free Cursor test double used by AWR-GT-001 and AWR-GT-003."""

    name = "cursor:recording"

    def __init__(
        self, plan_result: str = "# Recorded implementation plan\n\n1. Inspect the code."
    ) -> None:
        self.dispatches: list[PlanningDispatch] = []
        self.execution_dispatches: list[ExecutionDispatch] = []
        self.plan_result = plan_result
        self._runs: dict[str, str] = {}
        self._execution_runs: dict[str, ExecutionAcknowledgement] = {}
        self._polls: dict[str, int] = {}
        self.force_ambiguous = False
        self.fail_submit = False

    def submit_for_planning(self, dispatch: PlanningDispatch) -> ExecutorAcknowledgement:
        if dispatch.mode != "PLAN_ONLY":
            raise ValueError("RecordingCursorExecutor accepts PLAN_ONLY packets only.")
        self.dispatches.append(dispatch)
        agent_id = dispatch.existing_agent_id or f"cursor-agent-{uuid4()}"
        run_id = f"cursor-run-{uuid4()}"
        self._runs[run_id] = agent_id
        return ExecutorAcknowledgement(
            executor_agent_id=agent_id,
            executor_run_id=run_id,
            executor=self.name,
            executor_url=None,
            accepted=True,
            message="Cursor planning run accepted.",
        )

    def get_planning_run(self, executor_agent_id: str, executor_run_id: str) -> PlanningRunResult:
        if self._runs.get(executor_run_id) != executor_agent_id:
            raise KeyError(f"Unknown recording run: {executor_run_id}")
        return PlanningRunResult(
            executor_agent_id=executor_agent_id,
            executor_run_id=executor_run_id,
            executor=self.name,
            status=ExecutorRunStatus.FINISHED,
            result=self.plan_result,
            duration_ms=1,
        )

    def submit_for_execution(self, dispatch: ExecutionDispatch) -> ExecutionAcknowledgement:
        if self.fail_submit:
            raise TimeoutError("Recording executor submit timed out.")
        if self.force_ambiguous:
            raise AmbiguousAcceptance("Recording executor acceptance is ambiguous.")
        existing = self._execution_runs.get(dispatch.provider_idempotency_key)
        if existing is not None:
            return existing
        self.execution_dispatches.append(dispatch)
        agent_id = (
            dispatch.existing_agent_id or f"cursor-exec-{dispatch.provider_idempotency_key[:12]}"
        )
        run_id = f"cursor-xrun-{dispatch.provider_idempotency_key[:16]}"
        acknowledgement = ExecutionAcknowledgement(
            executor_agent_id=agent_id,
            executor_run_id=run_id,
            executor=self.name,
            accepted=True,
            message="Cursor execution run accepted.",
        )
        self._execution_runs[dispatch.provider_idempotency_key] = acknowledgement
        self._runs[run_id] = agent_id
        return acknowledgement

    def recover_execution_submission(
        self, dispatch: ExecutionDispatch
    ) -> ExecutionAcknowledgement | None:
        return self._execution_runs.get(dispatch.provider_idempotency_key)

    def get_execution_run(self, executor_agent_id: str, executor_run_id: str) -> ExecutionRunResult:
        if self._runs.get(executor_run_id) != executor_agent_id:
            raise KeyError(f"Unknown recording execution run: {executor_run_id}")
        seen = self._polls.get(executor_run_id, 0) + 1
        self._polls[executor_run_id] = seen
        if seen == 1:
            return ExecutionRunResult(
                executor_agent_id=executor_agent_id,
                executor_run_id=executor_run_id,
                executor=self.name,
                status=ExecutorRunStatus.RUNNING,
                result="@response-progress",
                duration_ms=1,
            )
        return ExecutionRunResult(
            executor_agent_id=executor_agent_id,
            executor_run_id=executor_run_id,
            executor=self.name,
            status=ExecutorRunStatus.FINISHED,
            result="@response-completed",
            duration_ms=2,
            git={
                "repository": "https://github.com/example/project",
                "branch": "cursor/awr-gt-003",
                "base_ref": "main",
                "commit_sha": "a" * 40,
            },
        )

    def capabilities(self) -> ExecutionCapabilities:
        return ExecutionCapabilities(
            follow_up_reuse=True,
            client_supplied_agent_id=True,
            run_idempotency_header=True,
            list_runs=True,
        )
