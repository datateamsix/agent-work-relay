from __future__ import annotations

from ..contracts import ExecutorAcknowledgement, PlanningDispatch


class CursorCloudExecutor:
    """Integration seam for the real Cursor Cloud Agent API.

    The adapter is intentionally not guessed into existence. Implement it against
    the selected Cursor endpoint, authentication scheme, and plan-mode contract,
    then run the same conformance tests as RecordingCursorExecutor.
    """

    name = "cursor:cloud"

    def submit_for_planning(self, dispatch: PlanningDispatch) -> ExecutorAcknowledgement:
        raise NotImplementedError(
            "Configure and implement the Cursor Cloud adapter before selecting it."
        )
