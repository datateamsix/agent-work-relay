from __future__ import annotations

from awr.contracts import WorkStatus

from .errors import TransitionError
from .events import LifecycleEvent

# Smallest operational graph. CANCEL is a family rule, not one row per state.
# REVIEW_COMPLETED and QUESTION_ANSWER are resolved in the kernel.
TRANSITION_TABLE: dict[tuple[WorkStatus, LifecycleEvent], WorkStatus] = {
    (WorkStatus.PLANNING, LifecycleEvent.PLAN_COMPLETED): WorkStatus.PLAN_READY,
    (WorkStatus.PLANNING, LifecycleEvent.QUESTION_BLOCKED): WorkStatus.WAITING_FOR_INPUT,
    (
        WorkStatus.PLAN_READY,
        LifecycleEvent.PLAN_APPROVAL_REQUESTED,
    ): WorkStatus.WAITING_FOR_PLAN_APPROVAL,
    (
        WorkStatus.WAITING_FOR_PLAN_APPROVAL,
        LifecycleEvent.APPROVE_PLAN,
    ): WorkStatus.READY_FOR_EXECUTION,
    (WorkStatus.WAITING_FOR_PLAN_APPROVAL, LifecycleEvent.REJECT_PLAN): WorkStatus.PLAN_READY,
    (WorkStatus.READY_FOR_EXECUTION, LifecycleEvent.PLAN_EXECUTE): WorkStatus.EXECUTION_DISPATCHED,
    (
        WorkStatus.EXECUTION_DISPATCHED,
        LifecycleEvent.EXECUTION_ACKNOWLEDGED,
    ): WorkStatus.EXECUTING,
    (WorkStatus.EXECUTING, LifecycleEvent.EXECUTION_PROGRESS): WorkStatus.EXECUTING,
    (WorkStatus.EXECUTING, LifecycleEvent.EXECUTION_COMPLETED): WorkStatus.COMPLETION_READY,
    (WorkStatus.EXECUTING, LifecycleEvent.EXECUTION_FAILED): WorkStatus.FAILED,
    (WorkStatus.EXECUTING, LifecycleEvent.QUESTION_BLOCKED): WorkStatus.WAITING_FOR_INPUT,
    (WorkStatus.COMPLETION_READY, LifecycleEvent.COMPLETION_REVIEW): WorkStatus.PLANNER_REVIEWING,
    (WorkStatus.PLANNER_REVIEWING, LifecycleEvent.REVIEW_COMPLETED): WorkStatus.PLANNER_REVIEWING,
    (
        WorkStatus.WAITING_FOR_HUMAN_REVIEW,
        LifecycleEvent.ACCEPT_COMPLETION,
    ): WorkStatus.COMPLETE,
    (
        WorkStatus.WAITING_FOR_HUMAN_REVIEW,
        LifecycleEvent.REQUEST_REVISION,
    ): WorkStatus.REVISION_REQUIRED,
    (
        WorkStatus.WAITING_FOR_INPUT,
        LifecycleEvent.QUESTION_ANSWER,
    ): WorkStatus.WAITING_FOR_INPUT,
    (
        WorkStatus.REVISION_REQUIRED,
        LifecycleEvent.IMPLEMENTATION_REFINE,
    ): WorkStatus.EXECUTION_DISPATCHED,
}

REVIEW_OUTCOMES = {
    "APPROVED": WorkStatus.WAITING_FOR_HUMAN_REVIEW,
    "REJECTED": WorkStatus.WAITING_FOR_HUMAN_REVIEW,
    "REVISE": WorkStatus.REVISION_REQUIRED,
}

CANCELLABLE = frozenset(
    {
        WorkStatus.PLANNING,
        WorkStatus.PLAN_READY,
        WorkStatus.WAITING_FOR_PLAN_APPROVAL,
        WorkStatus.READY_FOR_EXECUTION,
        WorkStatus.EXECUTION_DISPATCHED,
        WorkStatus.EXECUTING,
        WorkStatus.COMPLETION_READY,
        WorkStatus.PLANNER_REVIEWING,
        WorkStatus.REVISION_REQUIRED,
        WorkStatus.WAITING_FOR_HUMAN_REVIEW,
        WorkStatus.WAITING_FOR_INPUT,
    }
)


def next_state(status: WorkStatus, event: LifecycleEvent) -> WorkStatus:
    if event is LifecycleEvent.CANCEL:
        if status not in CANCELLABLE:
            raise TransitionError(f"Transition {status.value} --{event.value}--> is not permitted.")
        return WorkStatus.CANCELLED
    try:
        return TRANSITION_TABLE[(status, event)]
    except KeyError as exc:
        raise TransitionError(
            f"Transition {status.value} --{event.value}--> is not permitted."
        ) from exc


def allowed_events(status: WorkStatus) -> frozenset[LifecycleEvent]:
    events = {event for (from_status, event) in TRANSITION_TABLE if from_status is status}
    if status in CANCELLABLE:
        events.add(LifecycleEvent.CANCEL)
    return frozenset(events)
