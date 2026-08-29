from __future__ import annotations

from dataclasses import dataclass, replace

from awr.contracts import WorkStatus
from awr.responses.contracts import ResponsePacket, ResponseType

from .decisions import (
    DecisionType,
    StoredDecision,
    decision_is_expired,
    matching_plan_approval,
)
from .errors import AuthorityError, LineageError, TransitionError
from .events import DECISION_EVENTS, LifecycleEvent
from .transitions import REVIEW_OUTCOMES, next_state


@dataclass(frozen=True, slots=True)
class LifecycleSnapshot:
    work_order_id: str
    source_input_sha256: str
    current_parent_id: str
    version: int
    participants: frozenset[str]
    plan_id: str | None = None
    plan_sha256: str | None = None
    bound_agent_id: str | None = None
    bound_run_id: str | None = None
    blocked_from: WorkStatus | None = None
    execution_acknowledged: bool = False
    latest_review_outcome: str | None = None
    decision_principals: frozenset[str] = frozenset()
    executor_principals: frozenset[str] = frozenset()
    original_lineage: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "work_order_id": self.work_order_id,
            "source_input_sha256": self.source_input_sha256,
            "current_parent_id": self.current_parent_id,
            "version": self.version,
            "participants": sorted(self.participants),
            "plan_id": self.plan_id,
            "plan_sha256": self.plan_sha256,
            "bound_agent_id": self.bound_agent_id,
            "bound_run_id": self.bound_run_id,
            "blocked_from": None if self.blocked_from is None else self.blocked_from.value,
            "execution_acknowledged": self.execution_acknowledged,
            "latest_review_outcome": self.latest_review_outcome,
            "decision_principals": sorted(self.decision_principals),
            "executor_principals": sorted(self.executor_principals),
            "original_lineage": list(self.original_lineage),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> LifecycleSnapshot:
        blocked = payload.get("blocked_from")
        lineage = payload.get("original_lineage") or []
        participants = payload.get("participants") or []
        return cls(
            work_order_id=str(payload["work_order_id"]),
            source_input_sha256=str(payload["source_input_sha256"]),
            current_parent_id=str(payload["current_parent_id"]),
            version=int(str(payload["version"])),
            participants=frozenset(str(item) for item in participants)
            if isinstance(participants, list)
            else frozenset(),
            plan_id=str(payload["plan_id"]) if payload.get("plan_id") else None,
            plan_sha256=str(payload["plan_sha256"]) if payload.get("plan_sha256") else None,
            bound_agent_id=str(payload["bound_agent_id"])
            if payload.get("bound_agent_id")
            else None,
            bound_run_id=str(payload["bound_run_id"]) if payload.get("bound_run_id") else None,
            blocked_from=WorkStatus(str(blocked)) if blocked else None,
            execution_acknowledged=bool(payload.get("execution_acknowledged", False)),
            latest_review_outcome=(
                str(payload["latest_review_outcome"])
                if payload.get("latest_review_outcome")
                else None
            ),
            decision_principals=_string_set(payload.get("decision_principals")),
            executor_principals=_string_set(payload.get("executor_principals")),
            original_lineage=tuple(str(item) for item in lineage)
            if isinstance(lineage, list)
            else (),
        )


@dataclass(frozen=True, slots=True)
class TransitionResult:
    status: WorkStatus
    snapshot: LifecycleSnapshot
    event: LifecycleEvent
    ledger_event: str


_RESPONSE_EVENT = {
    ResponseType.PLAN_COMPLETED: LifecycleEvent.PLAN_COMPLETED,
    ResponseType.QUESTION_BLOCKED: LifecycleEvent.QUESTION_BLOCKED,
    ResponseType.EXECUTION_ACKNOWLEDGED: LifecycleEvent.EXECUTION_ACKNOWLEDGED,
    ResponseType.EXECUTION_PROGRESS: LifecycleEvent.EXECUTION_PROGRESS,
    ResponseType.EXECUTION_COMPLETED: LifecycleEvent.EXECUTION_COMPLETED,
    ResponseType.EXECUTION_FAILED: LifecycleEvent.EXECUTION_FAILED,
    ResponseType.REVIEW_COMPLETED: LifecycleEvent.REVIEW_COMPLETED,
}


def derive_snapshot(
    work_order_id: str, sender: str, recipient: str, source_input_sha256: str
) -> LifecycleSnapshot:
    return LifecycleSnapshot(
        work_order_id=work_order_id,
        source_input_sha256=source_input_sha256,
        current_parent_id=work_order_id,
        version=1,
        participants=frozenset({sender, recipient, "broker:awr"}),
        decision_principals=frozenset({sender}),
        executor_principals=frozenset({recipient}),
        original_lineage=(work_order_id,),
    )


def apply_response(
    *,
    status: WorkStatus,
    snapshot: LifecycleSnapshot,
    packet: ResponsePacket,
    actor: str,
    decisions: tuple[StoredDecision, ...],
    expected_version: int | None = None,
) -> TransitionResult:
    _assert_actor(actor, snapshot)
    _assert_version(snapshot, expected_version)
    if packet.work_order_id != snapshot.work_order_id:
        raise LineageError("Response work-order ID does not match the lineage.")
    if packet.in_reply_to != snapshot.current_parent_id:
        raise LineageError("Response must reference the immediate parent message or packet.")
    if packet.source_input_sha256 != snapshot.source_input_sha256:
        raise LineageError("source_input_sha256 does not match the accepted input.")
    if packet.actor and packet.actor != actor:
        raise AuthorityError("Response actor does not match the authenticated participant.")
    event = _RESPONSE_EVENT.get(packet.response_type)
    if event is None:
        raise TransitionError(
            f"{packet.response_type.value} is not an operational lifecycle response."
        )
    target = _response_target(status, event, packet)
    plan_id = snapshot.plan_id
    plan_sha256 = snapshot.plan_sha256
    bound_agent = snapshot.bound_agent_id
    bound_run = snapshot.bound_run_id
    acknowledged = snapshot.execution_acknowledged
    blocked_from = snapshot.blocked_from
    latest_review_outcome = snapshot.latest_review_outcome
    executor_principals = snapshot.executor_principals
    if packet.response_type is ResponseType.PLAN_COMPLETED:
        plan_id = str(packet.payload.get("plan_id") or packet.message_id or packet.content_sha256)
        plan_sha256 = packet.content_sha256
    if packet.response_type is ResponseType.EXECUTION_ACKNOWLEDGED:
        matching_plan_approval(
            decisions,
            plan_id=plan_id or "",
            plan_sha256=plan_sha256 or "",
        )
        acknowledged = True
        bound_run = packet.executor_run_id or str(packet.payload.get("executor_run_id"))
        bound_agent = packet.actor or actor
        executor_principals = snapshot.executor_principals | {bound_agent}
    if packet.response_type in {
        ResponseType.EXECUTION_PROGRESS,
        ResponseType.EXECUTION_COMPLETED,
        ResponseType.EXECUTION_FAILED,
    }:
        if not snapshot.execution_acknowledged:
            raise LineageError("Provider run binding requires execution.acknowledged.")
        run_id = packet.executor_run_id or packet.payload.get("executor_run_id")
        if run_id != snapshot.bound_run_id:
            raise LineageError("Response is not bound to the acknowledged provider run.")
    if packet.response_type is ResponseType.QUESTION_BLOCKED:
        blocked_from = status
    if packet.response_type is ResponseType.REVIEW_COMPLETED:
        latest_review_outcome = str(packet.payload.get("outcome"))
    parent_id = packet.message_id or packet.content_sha256 or snapshot.current_parent_id
    updated = replace(
        snapshot,
        current_parent_id=str(parent_id),
        version=snapshot.version + 1,
        plan_id=plan_id,
        plan_sha256=plan_sha256,
        bound_agent_id=bound_agent,
        bound_run_id=bound_run,
        blocked_from=blocked_from,
        execution_acknowledged=acknowledged,
        latest_review_outcome=latest_review_outcome,
        executor_principals=executor_principals,
        original_lineage=snapshot.original_lineage or (snapshot.current_parent_id,),
    )
    return TransitionResult(status=target, snapshot=updated, event=event, ledger_event=event.value)


def apply_broker_event(
    *,
    status: WorkStatus,
    snapshot: LifecycleSnapshot,
    event: LifecycleEvent,
    actor: str,
    message_id: str,
    decisions: tuple[StoredDecision, ...],
    expected_version: int | None = None,
    plan_id: str | None = None,
    plan_sha256: str | None = None,
) -> TransitionResult:
    _assert_actor(actor, snapshot)
    _assert_version(snapshot, expected_version)
    if event is LifecycleEvent.PLAN_EXECUTE:
        if snapshot.plan_id and plan_id and plan_id != snapshot.plan_id:
            raise AuthorityError("Approval for one plan fingerprint cannot authorize another.")
        if snapshot.plan_sha256 and plan_sha256 and plan_sha256 != snapshot.plan_sha256:
            raise AuthorityError("Approval for one plan fingerprint cannot authorize another.")
        matching_plan_approval(
            decisions,
            plan_id=plan_id or snapshot.plan_id or "",
            plan_sha256=plan_sha256 or snapshot.plan_sha256 or "",
        )
    if event is LifecycleEvent.IMPLEMENTATION_REFINE:
        if snapshot.plan_id and plan_id and plan_id != snapshot.plan_id:
            raise AuthorityError("Approval for one plan fingerprint cannot authorize another.")
        if snapshot.plan_sha256 and plan_sha256 and plan_sha256 != snapshot.plan_sha256:
            raise AuthorityError("Approval for one plan fingerprint cannot authorize another.")
        matching_plan_approval(
            decisions,
            plan_id=plan_id or snapshot.plan_id or "",
            plan_sha256=plan_sha256 or snapshot.plan_sha256 or "",
        )
        if not snapshot.original_lineage:
            raise LineageError("Refinement must preserve the original lineage.")
    if event is LifecycleEvent.QUESTION_ANSWER:
        next_state(status, event)
        if snapshot.blocked_from is None:
            raise TransitionError("question.answer has no blocked origin state.")
        target = snapshot.blocked_from
        updated = replace(
            snapshot,
            current_parent_id=message_id,
            version=snapshot.version + 1,
            blocked_from=None,
        )
        return TransitionResult(
            status=target, snapshot=updated, event=event, ledger_event=event.value
        )
    target = next_state(status, event)
    updated = replace(
        snapshot,
        current_parent_id=message_id,
        version=snapshot.version + 1,
        original_lineage=snapshot.original_lineage or (snapshot.current_parent_id,),
    )
    return TransitionResult(status=target, snapshot=updated, event=event, ledger_event=event.value)


def apply_decision(
    *,
    status: WorkStatus,
    snapshot: LifecycleSnapshot,
    event: LifecycleEvent,
    decision: StoredDecision,
    expected_version: int | None = None,
) -> TransitionResult:
    _assert_actor(decision.actor, snapshot)
    _assert_version(snapshot, expected_version)
    if event not in DECISION_EVENTS:
        raise AuthorityError(f"{event.value} is not a stored decision.")
    _assert_decision_principal(decision.actor, snapshot)
    if decision_is_expired(decision):
        raise AuthorityError("The stored decision has expired.")
    if not decision.rationale.strip():
        raise AuthorityError("A compact decision rationale is required.")
    if decision.work_order_id != snapshot.work_order_id:
        raise LineageError("Decision work-order ID does not match the lineage.")
    if snapshot.blocked_from is not None and event in {
        LifecycleEvent.ACCEPT_COMPLETION,
        LifecycleEvent.REQUEST_REVISION,
    }:
        raise TransitionError(
            "A blocking question must be answered before completion or revision decisions."
        )
    if event is LifecycleEvent.APPROVE_PLAN:
        if decision.decision_type is not DecisionType.APPROVE_PLAN:
            raise AuthorityError("approve_plan requires a stored approve_plan decision.")
        if not snapshot.plan_id or not snapshot.plan_sha256:
            raise AuthorityError("A plan packet must exist before it can be approved.")
        if decision.target_id != snapshot.plan_id or decision.target_sha256.removeprefix(
            "sha256:"
        ) != snapshot.plan_sha256.removeprefix("sha256:"):
            raise AuthorityError("Approval for one plan fingerprint cannot authorize another.")
    target = next_state(status, event)
    updated = replace(snapshot, version=snapshot.version + 1)
    return TransitionResult(status=target, snapshot=updated, event=event, ledger_event=event.value)


def _response_target(
    status: WorkStatus, event: LifecycleEvent, packet: ResponsePacket
) -> WorkStatus:
    if status.terminal:
        raise TransitionError(f"Terminal state {status.value} accepts no responses.")
    if event is LifecycleEvent.REVIEW_COMPLETED:
        next_state(status, event)
        outcome = str(packet.payload.get("outcome"))
        try:
            return REVIEW_OUTCOMES[outcome]
        except KeyError as exc:
            raise TransitionError(f"Unknown review outcome: {outcome}.") from exc
    return next_state(status, event)


def _assert_actor(actor: str, snapshot: LifecycleSnapshot) -> None:
    if not actor:
        raise AuthorityError("Authenticated participant identity is required.")
    if actor not in snapshot.participants:
        raise AuthorityError(f"{actor} is not a work-order participant.")


def _assert_decision_principal(actor: str, snapshot: LifecycleSnapshot) -> None:
    if actor == snapshot.bound_agent_id or actor in snapshot.executor_principals:
        raise AuthorityError("Executor identities cannot record human decisions.")
    if actor not in snapshot.decision_principals:
        raise AuthorityError("Only a decision principal may record human decisions.")


def _string_set(value: object) -> frozenset[str]:
    if not isinstance(value, list):
        return frozenset()
    return frozenset(str(item) for item in value)


def _assert_version(snapshot: LifecycleSnapshot, expected_version: int | None) -> None:
    if expected_version is not None and expected_version != snapshot.version:
        raise LineageError("expected work-order version does not match the current snapshot.")
