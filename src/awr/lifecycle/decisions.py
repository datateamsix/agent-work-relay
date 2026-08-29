from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from awr.responses.canonical import canonical_json, fingerprint_bytes

from .errors import AuthorityError

MAX_DECISION_RATIONALE_BYTES = 512
_FINGERPRINT_FIELDS = (
    "decision_type",
    "work_order_id",
    "actor",
    "target_kind",
    "target_id",
    "target_sha256",
    "permitted_action",
    "scope",
    "idempotency_key",
    "rationale",
    "expires_at",
)


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
    rationale: str
    expires_at: str | None = None

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
            "rationale": self.rationale,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> StoredDecision:
        expires = payload.get("expires_at")
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
            rationale=str(payload.get("rationale") or ""),
            expires_at=None if expires in (None, "") else str(expires),
        )


def require_rationale(rationale: str) -> str:
    text = rationale.strip()
    if not text:
        raise AuthorityError("A compact decision rationale is required.")
    if len(text.encode("utf-8")) > MAX_DECISION_RATIONALE_BYTES:
        raise AuthorityError(
            f"Decision rationale exceeds the {MAX_DECISION_RATIONALE_BYTES} byte compact limit."
        )
    return text


def decision_is_expired(decision: StoredDecision, *, now: datetime | None = None) -> bool:
    if not decision.expires_at:
        return False
    raw = decision.expires_at
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    clock = now or datetime.now(UTC)
    return parsed <= clock


def fingerprint_decision(decision: StoredDecision | dict[str, Any]) -> str:
    payload = decision.to_dict() if isinstance(decision, StoredDecision) else dict(decision)
    body: dict[str, Any] = {key: payload.get(key) for key in _FINGERPRINT_FIELDS}
    if body.get("target_sha256") is not None:
        body["target_sha256"] = str(body["target_sha256"]).removeprefix("sha256:")
    return fingerprint_bytes(canonical_json(body))


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
        and not decision_is_expired(decision)
    ]
    if not matches:
        raise AuthorityError(
            "Execution requires a stored decision bound to the exact plan ID and SHA-256."
        )
    return matches[-1]
