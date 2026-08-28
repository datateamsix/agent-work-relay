from __future__ import annotations

from typing import Protocol

from ..contracts import ExecutorAcknowledgement, PlanningDispatch


class PlanningExecutor(Protocol):
    name: str

    def submit_for_planning(self, dispatch: PlanningDispatch) -> ExecutorAcknowledgement: ...
