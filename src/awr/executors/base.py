from __future__ import annotations

from typing import Protocol

from ..contracts import ExecutorAcknowledgement, PlanningDispatch, PlanningRunResult


class PlanningExecutor(Protocol):
    name: str

    def submit_for_planning(self, dispatch: PlanningDispatch) -> ExecutorAcknowledgement: ...

    def get_planning_run(
        self, executor_agent_id: str, executor_run_id: str
    ) -> PlanningRunResult: ...
