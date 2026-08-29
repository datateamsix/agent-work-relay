from __future__ import annotations

from enum import StrEnum


class LifecycleEvent(StrEnum):
    PLAN_COMPLETED = "plan.completed"
    PLAN_APPROVAL_REQUESTED = "plan.approval_requested"
    APPROVE_PLAN = "decision.approve_plan"
    REJECT_PLAN = "decision.reject_plan"
    PLAN_EXECUTE = "plan.execute"
    EXECUTION_ACKNOWLEDGED = "execution.acknowledged"
    EXECUTION_PROGRESS = "execution.progress"
    EXECUTION_COMPLETED = "execution.completed"
    EXECUTION_FAILED = "execution.failed"
    COMPLETION_REVIEW = "completion.review"
    REVIEW_COMPLETED = "review.completed"
    ACCEPT_COMPLETION = "decision.accept_completion"
    REQUEST_REVISION = "decision.request_revision"
    IMPLEMENTATION_REFINE = "implementation.refine"
    QUESTION_BLOCKED = "question.blocked"
    QUESTION_ANSWER = "question.answer"
    CANCEL = "decision.cancel"


RESPONSE_EVENTS = frozenset(
    {
        LifecycleEvent.PLAN_COMPLETED,
        LifecycleEvent.QUESTION_BLOCKED,
        LifecycleEvent.EXECUTION_ACKNOWLEDGED,
        LifecycleEvent.EXECUTION_PROGRESS,
        LifecycleEvent.EXECUTION_COMPLETED,
        LifecycleEvent.EXECUTION_FAILED,
        LifecycleEvent.REVIEW_COMPLETED,
    }
)

DECISION_EVENTS = frozenset(
    {
        LifecycleEvent.APPROVE_PLAN,
        LifecycleEvent.REJECT_PLAN,
        LifecycleEvent.ACCEPT_COMPLETION,
        LifecycleEvent.REQUEST_REVISION,
        LifecycleEvent.CANCEL,
    }
)

BROKER_EVENTS = frozenset(
    {
        LifecycleEvent.PLAN_APPROVAL_REQUESTED,
        LifecycleEvent.PLAN_EXECUTE,
        LifecycleEvent.COMPLETION_REVIEW,
        LifecycleEvent.IMPLEMENTATION_REFINE,
        LifecycleEvent.QUESTION_ANSWER,
    }
)
