from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .errors import AuthorityError


class DecisionType(StrEnum):
    APPROVE_PLAN = "approve_plan"
    REJECT_PLAN = "reject_plan"
    ACCEPT_COMPLETION = "accept_completion"
    REQUEST_REVISION = "request_revision"
    CANCEL = "cancel"
    AUTHORIZE_MERGE = "authorize_merge"
    AUTHORIZE_MAIN_PUSH = "authorize_main_push"
    AUTHORIZE_DEPLOYMENT = "authorize_deployment"
    AUTHORIZE_DESTRUCTIVE = "authorize_destructive"


class DecisionTargetKind(StrEnum):
    PLAN = "plan"
    COMPLETION = "completion"
    REVIEW = "review"
    WORK_ORDER = "work_order"


@dataclass(frozen=True, slots=True)
class StoredDecision:
    decision_id: str
    decision_type: DecisionType
    work_order_id: str
    actor: str
    target_kind: DecisionTargetKind
    target_id: str
    target_sha256: str
    permitted_action: str
    scope: str
    created_at: str
    idempotency_key: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "decision_type": self.decision_type.value,
            "work_order_id": self.work_order_id,
            "actor": self.actor,
            "target_kind": self.target_kind.value,
            "target_id": self.target_id,
            "target_sha256": self.target_sha256,
            "permitted_action": self.permitted_action,
            "scope": self.scope,
            "created_at": self.created_at,
            "idempotency_key": self.idempotency_key,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> StoredDecision:
        return cls(
            decision_id=str(payload["decision_id"]),
            decision_type=DecisionType(str(payload["decision_type"])),
            work_order_id=str(payload["work_order_id"]),
            actor=str(payload["actor"]),
            target_kind=DecisionTargetKind(str(payload["target_kind"])),
            target_id=str(payload["target_id"]),
            target_sha256=str(payload["target_sha256"]),
            permitted_action=str(payload["permitted_action"]),
            scope=str(payload["scope"]),
            created_at=str(payload["created_at"]),
            idempotency_key=str(payload["idempotency_key"]),
        )


def matching_plan_approval(
    decisions: tuple[StoredDecision, ...],
    *,
    plan_id: str,
    plan_sha256: str,
) -> StoredDecision:
    digest = plan_sha256.removeprefix("sha256:")
    matches = [
        decision
        for decision in decisions
        if decision.decision_type is DecisionType.APPROVE_PLAN
        and decision.target_kind is DecisionTargetKind.PLAN
        and decision.target_id == plan_id
        and decision.target_sha256.removeprefix("sha256:") == digest
    ]
    if not matches:
        raise AuthorityError(
            "Execution requires a stored decision bound to the exact plan ID and SHA-256."
        )
    return matches[-1]
