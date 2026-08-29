from __future__ import annotations

from awr.contracts import WorkStatus


def pending_actions(status: WorkStatus, *, blocked: bool = False) -> list[dict[str, str]]:
    mapping: dict[WorkStatus, list[dict[str, str]]] = {
        WorkStatus.PLAN_READY: [
            {
                "action": "plan.approval_requested",
                "kind": "broker_event",
                "summary": "Request human approval for the stored plan fingerprint.",
            }
        ],
        WorkStatus.WAITING_FOR_PLAN_APPROVAL: [
            {
                "action": "approve_plan",
                "kind": "decision",
                "summary": "Approve the exact stored plan ID and SHA-256.",
            },
            {
                "action": "reject_plan",
                "kind": "decision",
                "summary": "Reject the current plan fingerprint.",
            },
        ],
        WorkStatus.READY_FOR_EXECUTION: [
            {
                "action": "plan.execute",
                "kind": "broker_event",
                "summary": "Dispatch execution after stored plan approval.",
            }
        ],
        WorkStatus.EXECUTION_DISPATCHED: [
            {
                "action": "execution.acknowledged",
                "kind": "response",
                "summary": "Bind the durable provider run before work begins.",
            }
        ],
        WorkStatus.EXECUTING: [
            {
                "action": "execution.progress",
                "kind": "response",
                "summary": "Optional meaningful checkpoint.",
            },
            {
                "action": "execution.completed",
                "kind": "response",
                "summary": "Terminal successful execution packet.",
            },
        ],
        WorkStatus.COMPLETION_READY: [
            {
                "action": "completion.review",
                "kind": "broker_event",
                "summary": "Request a completion review.",
            }
        ],
        WorkStatus.PLANNER_REVIEWING: [
            {
                "action": "review.completed",
                "kind": "response",
                "summary": "Record a review recommendation. This does not close the work order.",
            }
        ],
        WorkStatus.WAITING_FOR_HUMAN_REVIEW: [
            {
                "action": "accept_completion",
                "kind": "decision",
                "summary": "Human acceptance closes the work order.",
            },
            {
                "action": "request_revision",
                "kind": "decision",
                "summary": "Request bounded follow-up in the same lineage.",
            },
        ],
        WorkStatus.REVISION_REQUIRED: [
            {
                "action": "implementation.refine",
                "kind": "broker_event",
                "summary": "Dispatch refinement against the approved plan fingerprint.",
            }
        ],
    }
    actions = list(mapping.get(status, []))
    if status is WorkStatus.WAITING_FOR_HUMAN_REVIEW and blocked:
        actions.insert(
            0,
            {
                "action": "question.answer",
                "kind": "broker_event",
                "summary": "Answer the blocking question and resume the prior state.",
            },
        )
    return actions
