from __future__ import annotations

from uuid import uuid4

from ..contracts import (
    ExecutorAcknowledgement,
    ExecutorRunStatus,
    PlanningDispatch,
    PlanningRunResult,
)


class RecordingCursorExecutor:
    """Credential-free Cursor test double used by AWR-GT-001."""

    name = "cursor:recording"

    def __init__(
        self, plan_result: str = "# Recorded implementation plan\n\n1. Inspect the code."
    ) -> None:
        self.dispatches: list[PlanningDispatch] = []
        self.plan_result = plan_result
        self._runs: dict[str, str] = {}

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
