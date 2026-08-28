from __future__ import annotations

from uuid import uuid4

from ..contracts import ExecutorAcknowledgement, PlanningDispatch


class RecordingCursorExecutor:
    """Credential-free Cursor test double used by EWB-GT-001."""

    name = "cursor:recording"

    def __init__(self) -> None:
        self.dispatches: list[PlanningDispatch] = []

    def submit_for_planning(self, dispatch: PlanningDispatch) -> ExecutorAcknowledgement:
        if dispatch.mode != "PLAN_ONLY":
            raise ValueError("RecordingCursorExecutor accepts PLAN_ONLY packets only.")
        self.dispatches.append(dispatch)
        return ExecutorAcknowledgement(
            executor_run_id=f"cursor-run-{uuid4()}",
            executor=self.name,
            accepted=True,
            message="Cursor planning run accepted.",
        )
