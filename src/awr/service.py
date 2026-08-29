from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from logging import getLogger
from typing import Any, BinaryIO
from urllib.parse import urlparse
from uuid import uuid4

from .artifacts.bundle import (
    BundleValidationError,
    reference_payloads,
    resolve_bundle,
    verify_bundle_generation,
)
from .artifacts.contracts import ArtifactStatus, WorkBundle
from .artifacts.relay import ArtifactRelay
from .auth.context import resolve_actor
from .contracts import (
    ExecutorRunStatus,
    LedgerEntry,
    PlanningDispatch,
    PlanningStatusReceipt,
    PlanPacket,
    SubmissionReceipt,
    WorkOrder,
    WorkStatus,
)
from .decorators import parse_directive
from .executors.base import PlanningExecutor
from .executors.execution import (
    AmbiguousAcceptance,
    DispatchState,
    ExecutionAcknowledgement,
    ExecutionDispatch,
    ExecutionExecutor,
    ExecutionRunResult,
)
from .lifecycle.decisions import (
    DecisionTargetKind,
    DecisionType,
    StoredDecision,
    fingerprint_decision,
    matching_plan_approval,
    require_rationale,
)
from .lifecycle.errors import IdempotencyConflict, LifecycleError
from .lifecycle.events import LifecycleEvent
from .lifecycle.kernel import (
    LifecycleSnapshot,
    apply_broker_event,
    apply_decision,
    apply_response,
    derive_snapshot,
)
from .lifecycle.pending import pending_actions
from .observability import log_event
from .responses.cache import replay_cache_key, response_idempotency_cache_key
from .responses.canonical import ResponsePacketError, fingerprint_packet
from .responses.contracts import (
    MAX_BODY_BYTES,
    RESPONSE_AUTHORITY,
    RESPONSE_SCHEMA,
    ResponsePacket,
    ResponseType,
)
from .responses.render import render_response_markdown
from .responses.validate import parse_response_markdown, parse_response_packet
from .storage.base import StateStore, WorkOrderSession
from .wrappers import WrappedPrompt, wrap_execution, wrap_prompt

_LOGGER = getLogger("awr")
_LEASE_TTL_SECONDS = 30.0

MAX_MARKDOWN_BYTES = 512 * 1024


class WorkOrderValidationError(ValueError):
    """A work order cannot be accepted without violating a broker invariant."""


class BrokerService:
    def __init__(
        self,
        store: StateStore,
        executor: PlanningExecutor,
        *,
        default_repository_url: str | None = None,
        default_base_ref: str = "main",
        artifacts: ArtifactRelay | None = None,
    ) -> None:
        self.store = store
        self.executor = executor
        self.default_repository_url = default_repository_url
        self.default_base_ref = default_base_ref
        self.artifacts = artifacts

    def submit_prompt_for_planning(
        self,
        *,
        markdown: str,
        sender: str,
        recipient: str,
        repository_url: str | None = None,
        base_ref: str | None = None,
        idempotency_key: str | None = None,
    ) -> SubmissionReceipt:
        self._reject_oversized_markdown(markdown)
        directive = parse_directive(markdown)
        parent = self._resolve_parent(directive.parent_work_order_id)
        resolved_repository_url, resolved_base_ref = self._resolve_repository(
            repository_url=repository_url,
            base_ref=base_ref,
            parent=parent,
        )
        content_sha256 = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        replay_key = idempotency_key or self._derive_idempotency_key(
            sender=sender,
            recipient=recipient,
            directive=directive.name,
            parent=directive.parent_work_order_id,
            repository_url=resolved_repository_url,
            base_ref=resolved_base_ref,
            content_sha256=content_sha256,
            bundle_sha256="",
        )
        work_order_id = f"AWR-{uuid4()}"
        wrapped = wrap_prompt(directive, markdown, work_order_id)
        candidate = WorkOrder(
            work_order_id=work_order_id,
            idempotency_key=replay_key,
            sender=sender,
            recipient=recipient,
            kind=directive.kind,
            action=directive.action,
            parent_work_order_id=directive.parent_work_order_id,
            repository_url=resolved_repository_url,
            base_ref=resolved_base_ref,
            markdown=markdown,
            content_sha256=content_sha256,
            wrapper_id=wrapped.wrapper_id,
            wrapper_version=wrapped.wrapper_version,
            wrapper_sha256=wrapped.wrapper_sha256,
            status=WorkStatus.ACCEPTED,
            created_at=datetime.now(UTC).isoformat(),
        )

        work_order, created, ledger_sequence = self.store.create_work_order(candidate)
        if not created:
            self._validate_replay(candidate, work_order)
            acknowledged = self._latest_event(work_order.work_order_id, "executor.acknowledged")
            if acknowledged is not None:
                return SubmissionReceipt(
                    receipt_type="work_order.accepted",
                    work_order_id=work_order.work_order_id,
                    content_sha256=work_order.content_sha256,
                    status=work_order.status,
                    duplicate=True,
                    executor_run_id=str(acknowledged.payload["executor_run_id"]),
                    ledger_sequence=ledger_sequence,
                    bundle_sha256=work_order.bundle_sha256,
                )
            wrapped = wrap_prompt(directive, markdown, work_order.work_order_id)

        return self._route_and_dispatch(work_order=work_order, wrapped=wrapped, parent=parent)

    def begin_artifact_intake(
        self,
        *,
        owner: str | None = None,
        original_filename: str,
        declared_media_type: str,
        purpose: str,
        idempotency_key: str,
        expected_byte_length: int | None = None,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        actor = self._actor(owner)
        return self._require_artifacts().begin_intake(
            owner=actor,
            original_filename=original_filename,
            declared_media_type=declared_media_type,
            purpose=purpose,
            idempotency_key=idempotency_key,
            expected_byte_length=expected_byte_length,
            expected_sha256=expected_sha256,
        )

    def upload_artifact_content(
        self,
        artifact_id: str,
        stream: BinaryIO,
        *,
        actor: str | None = None,
        token: str,
    ) -> dict[str, Any]:
        resolved = self._actor(actor)
        artifact = self._require_artifacts().upload_content(
            artifact_id, stream, actor=resolved, token=token
        )
        return {"artifact_id": artifact.artifact_id, "status": artifact.status.value}

    def finalize_artifact_upload(
        self, artifact_id: str, *, actor: str | None = None
    ) -> dict[str, Any]:
        return self._require_artifacts().finalize_upload(artifact_id, actor=self._actor(actor))

    def get_artifact_status(self, artifact_id: str, *, actor: str | None = None) -> dict[str, Any]:
        return self._require_artifacts().get_status(artifact_id, actor=self._actor(actor))

    def submit_work_bundle_for_planning(
        self,
        *,
        markdown: str,
        sender: str | None = None,
        recipient: str,
        repository_url: str | None = None,
        base_ref: str | None = None,
        idempotency_key: str | None = None,
        artifact_ids: list[str] | tuple[str, ...] | None = None,
    ) -> SubmissionReceipt:
        artifacts = self._require_artifacts()
        actor = self._actor(sender)
        try:
            directive = parse_directive(markdown)
            parent = self._resolve_parent(directive.parent_work_order_id)
            resolved_repository_url, resolved_base_ref = self._resolve_repository(
                repository_url=repository_url,
                base_ref=base_ref,
                parent=parent,
            )
            bundle = resolve_bundle(
                markdown,
                tuple(artifact_ids or ()),
                sender=actor,
                metadata=artifacts.metadata,
                bodies=artifacts.bodies,
            )
        except BundleValidationError as exc:
            raise WorkOrderValidationError(str(exc)) from exc
        content_sha256 = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        replay_key = idempotency_key or self._derive_idempotency_key(
            sender=actor,
            recipient=recipient,
            directive=directive.name,
            parent=directive.parent_work_order_id,
            repository_url=resolved_repository_url,
            base_ref=resolved_base_ref,
            content_sha256=content_sha256,
            bundle_sha256=bundle.bundle_sha256,
        )
        work_order_id = f"AWR-{uuid4()}"
        wrapped = wrap_prompt(directive, markdown, work_order_id, bundle.references)
        candidate = WorkOrder(
            work_order_id=work_order_id,
            idempotency_key=replay_key,
            sender=actor,
            recipient=recipient,
            kind=directive.kind,
            action=directive.action,
            parent_work_order_id=directive.parent_work_order_id,
            repository_url=resolved_repository_url,
            base_ref=resolved_base_ref,
            markdown=markdown,
            content_sha256=content_sha256,
            wrapper_id=wrapped.wrapper_id,
            wrapper_version=wrapped.wrapper_version,
            wrapper_sha256=wrapped.wrapper_sha256,
            status=WorkStatus.ACCEPTED,
            created_at=datetime.now(UTC).isoformat(),
            bundle_sha256=bundle.bundle_sha256,
        )
        work_order, created, ledger_sequence = self.store.create_work_order(candidate)
        if not created:
            self._validate_replay(candidate, work_order)
            acknowledged = self._latest_event(work_order.work_order_id, "executor.acknowledged")
            if acknowledged is not None:
                return SubmissionReceipt(
                    receipt_type="work_order.accepted",
                    work_order_id=work_order.work_order_id,
                    content_sha256=work_order.content_sha256,
                    status=work_order.status,
                    duplicate=True,
                    executor_run_id=str(acknowledged.payload["executor_run_id"]),
                    ledger_sequence=ledger_sequence,
                    bundle_sha256=work_order.bundle_sha256,
                )
            wrapped = wrap_prompt(directive, markdown, work_order.work_order_id, bundle.references)
        self._record_bundle(work_order, bundle, actor)
        try:
            verify_bundle_generation(
                bundle,
                sender=actor,
                metadata=artifacts.metadata,
                bodies=artifacts.bodies,
            )
        except BundleValidationError as exc:
            with self.store.lock_work_order(work_order.work_order_id) as session:
                if self._latest_from(session.list_ledger(), "work_order.routed") is None:
                    session.update_status(WorkStatus.FAILED)
                    session.append_ledger(
                        event_type="bundle.rejected",
                        actor="broker:awr",
                        counterparty=actor,
                        payload={"reason": str(exc), "bundle_sha256": bundle.bundle_sha256},
                    )
            raise WorkOrderValidationError(str(exc)) from exc
        return self._route_and_dispatch(work_order=work_order, wrapped=wrapped, parent=parent)

    def get_work_order_artifacts(
        self, work_order_id: str, *, actor: str | None = None
    ) -> list[dict[str, Any]]:
        self._authorize_projection(work_order_id, actor)
        validated = self._latest_event(work_order_id, "bundle.validated")
        if validated is None:
            return []
        raw = validated.payload.get("references")
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]

    def refresh_planning(self, work_order_id: str) -> PlanningStatusReceipt | PlanPacket:
        work_order = self._require_work_order(work_order_id)
        existing_plan = self._plan_from_ledger(work_order_id)
        if existing_plan is not None:
            return existing_plan

        with self.store.lock_work_order(work_order_id) as session:
            committed = self._commit_existing_plan(session)
            if committed is not None:
                return committed

        acknowledgement = self._latest_event(work_order_id, "executor.acknowledged")
        if acknowledgement is None:
            raise WorkOrderValidationError(
                f"Work order {work_order_id} has no executor acknowledgement."
            )
        executor_agent_id = str(acknowledgement.payload["executor_agent_id"])
        executor_run_id = str(acknowledgement.payload["executor_run_id"])
        run = self.executor.get_planning_run(executor_agent_id, executor_run_id)

        if not run.status.terminal:
            with self.store.lock_work_order(work_order_id) as session:
                committed = self._commit_existing_plan(session)
                if committed is not None:
                    return committed
                status_event = self._latest_from(session.list_ledger(), "executor.status")
                if status_event is None or status_event.payload.get("status") != run.status.value:
                    status_event = session.append_ledger(
                        event_type="executor.status",
                        actor=run.executor,
                        counterparty="broker:awr",
                        payload={
                            "executor_agent_id": executor_agent_id,
                            "executor_run_id": executor_run_id,
                            "status": run.status.value,
                        },
                    )
                return PlanningStatusReceipt(
                    work_order_id=work_order_id,
                    status=session.get_work_order().status,
                    executor_status=run.status,
                    executor_agent_id=executor_agent_id,
                    executor_run_id=executor_run_id,
                    ledger_sequence=status_event.sequence,
                )

        if run.status is not ExecutorRunStatus.FINISHED or run.result is None:
            with self.store.lock_work_order(work_order_id) as session:
                committed = self._commit_existing_plan(session)
                if committed is not None:
                    return committed
                failed = self._latest_from(session.list_ledger(), "executor.failed")
                if failed is None:
                    session.update_status(WorkStatus.FAILED)
                    failed = session.append_ledger(
                        event_type="executor.failed",
                        actor=run.executor,
                        counterparty="broker:awr",
                        payload={
                            "executor_agent_id": executor_agent_id,
                            "executor_run_id": executor_run_id,
                            "status": run.status.value,
                        },
                    )
                return PlanningStatusReceipt(
                    work_order_id=work_order_id,
                    status=WorkStatus.FAILED,
                    executor_status=run.status,
                    executor_agent_id=executor_agent_id,
                    executor_run_id=executor_run_id,
                    ledger_sequence=failed.sequence,
                )

        content_sha256 = hashlib.sha256(run.result.encode("utf-8")).hexdigest()
        plan_id = f"PLAN-{content_sha256[:24]}"
        with self.store.lock_work_order(work_order_id) as session:
            committed = self._commit_existing_plan(session)
            if committed is not None:
                return committed
            received = session.append_ledger(
                event_type="plan.received",
                actor=run.executor,
                counterparty="broker:awr",
                payload={
                    "plan_id": plan_id,
                    "executor_agent_id": executor_agent_id,
                    "executor_run_id": executor_run_id,
                    "content": run.result,
                    "content_sha256": content_sha256,
                    "duration_ms": run.duration_ms,
                    "git": run.git,
                },
            )
            session.update_status(WorkStatus.PLAN_READY)
            available = session.append_ledger(
                event_type="plan.available",
                actor="broker:awr",
                counterparty=work_order.sender,
                payload={
                    "plan_id": plan_id,
                    "content_sha256": content_sha256,
                    "received_sequence": received.sequence,
                },
            )
            return self._plan_packet(work_order_id, received, available)

    def get_plan(self, work_order_id: str, *, actor: str | None = None) -> PlanPacket:
        self._authorize_projection(work_order_id, actor)
        plan = self._plan_from_ledger(work_order_id)
        if plan is not None:
            return plan
        with self.store.lock_work_order(work_order_id) as session:
            plan = self._commit_existing_plan(session)
        if plan is None:
            raise WorkOrderValidationError(f"Plan is not ready for work order {work_order_id}.")
        return plan

    def get_work_order_timeline(
        self, work_order_id: str, *, actor: str | None = None
    ) -> list[dict[str, Any]]:
        self._authorize_projection(work_order_id, actor)
        return [entry.to_dict() for entry in self.store.list_ledger(work_order_id)]

    def get_work_order(self, work_order_id: str, *, actor: str | None = None) -> dict[str, Any]:
        work_order = self._require_work_order(work_order_id)
        resolved = self._actor(actor)
        with self.store.lock_work_order(work_order_id) as session:
            snapshot = self._snapshot_from_session(session, work_order)
            self._authorize_reader(resolved, work_order, snapshot)
            decisions = [StoredDecision.from_dict(item) for item in session.list_decisions()]
        return {
            **work_order.to_dict(),
            "kind": work_order.kind.value,
            "action": work_order.action.value,
            "status": work_order.status.value,
            "lifecycle": snapshot.to_dict(),
            "pending_actions": pending_actions(
                work_order.status, blocked=snapshot.blocked_from is not None
            ),
            "decisions": [decision.to_dict() for decision in decisions],
            "plan_id": snapshot.plan_id,
            "plan_sha256": snapshot.plan_sha256,
        }

    def list_pending_actions(
        self, work_order_id: str | None = None, *, actor: str | None = None
    ) -> list[dict[str, Any]]:
        if work_order_id is not None:
            projection = self.get_work_order(work_order_id, actor=actor)
            return [
                {**item, "work_order_id": work_order_id, "status": projection["status"]}
                for item in projection["pending_actions"]
            ]
        resolved = self._actor(actor)
        pending: list[dict[str, Any]] = []
        for work_order in self.store.list_work_orders():
            try:
                projection = self.get_work_order(work_order.work_order_id, actor=resolved)
            except WorkOrderValidationError:
                continue
            pending.extend(
                {
                    **item,
                    "work_order_id": work_order.work_order_id,
                    "status": projection["status"],
                }
                for item in projection["pending_actions"]
            )
        return pending

    def submit_response(
        self,
        *,
        markdown: str,
        actor: str | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        resolved = self._actor(actor)
        try:
            packet = parse_response_markdown(markdown)
        except (ResponsePacketError, ValueError) as exc:
            raise WorkOrderValidationError(str(exc)) from exc
        if packet.authority != RESPONSE_AUTHORITY:
            raise WorkOrderValidationError("Responses may only carry report_only authority.")
        work_order = self._require_work_order(packet.work_order_id)
        self._validate_evidence(work_order, packet, resolved)
        try:
            with self.store.lock_work_order(packet.work_order_id) as session:
                snapshot = self._snapshot_from_session(session, work_order)
                decisions = tuple(
                    StoredDecision.from_dict(item) for item in session.list_decisions()
                )
                existing = session.get_response_by_idempotency(
                    resolved, packet.response_type.value, packet.idempotency_key
                )
                digest = packet.content_sha256 or fingerprint_packet(packet)
                message_id = packet.message_id or digest
                if existing is not None:
                    if existing["content_sha256"] != digest:
                        raise IdempotencyConflict(
                            "The idempotency key is already bound to a different canonical packet."
                        )
                    stored_receipt = existing.get("receipt")
                    if isinstance(stored_receipt, dict):
                        return stored_receipt
                    current = session.get_work_order()
                    return {
                        "receipt_type": "response.accepted",
                        "work_order_id": current.work_order_id,
                        "response_type": packet.response_type.value,
                        "content_sha256": existing["content_sha256"],
                        "status": current.status.value,
                        "duplicate": True,
                        "cache_key": response_idempotency_cache_key(
                            actor=resolved,
                            operation=packet.response_type.value,
                            idempotency_key=packet.idempotency_key,
                            packet_fingerprint=digest,
                        ),
                    }
                result = apply_response(
                    status=session.get_work_order().status,
                    snapshot=snapshot,
                    packet=packet,
                    actor=resolved,
                    decisions=decisions,
                    expected_version=expected_version,
                )
                session.update_status(result.status)
                entry = session.append_ledger(
                    event_type=result.ledger_event,
                    actor=resolved,
                    counterparty="broker:awr",
                    payload={
                        "message_id": message_id,
                        "content_sha256": digest,
                        "response_type": packet.response_type.value,
                    },
                )
                session.put_lifecycle(
                    {
                        **result.snapshot.to_dict(),
                        "current_parent_id": message_id,
                    }
                )
                receipt = {
                    "receipt_type": "response.accepted",
                    "work_order_id": packet.work_order_id,
                    "response_type": packet.response_type.value,
                    "content_sha256": digest,
                    "message_id": message_id,
                    "status": result.status.value,
                    "duplicate": False,
                    "ledger_sequence": entry.sequence,
                    "cache_key": response_idempotency_cache_key(
                        actor=resolved,
                        operation=packet.response_type.value,
                        idempotency_key=packet.idempotency_key,
                        packet_fingerprint=digest,
                    ),
                }
                session.put_response_packet(
                    {
                        "packet_id": message_id,
                        "response_type": packet.response_type.value,
                        "actor": resolved,
                        "idempotency_key": packet.idempotency_key,
                        "content_sha256": digest,
                        "in_reply_to": packet.in_reply_to,
                        "source_input_sha256": packet.source_input_sha256,
                        "created_at": packet.created_at,
                        "packet": packet.to_dict(),
                        "receipt": receipt,
                    }
                )
                return receipt
        except LifecycleError as exc:
            raise WorkOrderValidationError(str(exc)) from exc

    def record_decision(
        self,
        *,
        decision_type: str,
        work_order_id: str,
        actor: str | None = None,
        target_id: str,
        target_sha256: str,
        idempotency_key: str,
        permitted_action: str,
        rationale: str,
        scope: str = "restricted",
        target_kind: str = "plan",
        expires_at: str | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        resolved = self._actor(actor)
        try:
            stored_type = DecisionType(decision_type)
            event = {
                DecisionType.APPROVE_PLAN: LifecycleEvent.APPROVE_PLAN,
                DecisionType.REJECT_PLAN: LifecycleEvent.REJECT_PLAN,
                DecisionType.ACCEPT_COMPLETION: LifecycleEvent.ACCEPT_COMPLETION,
                DecisionType.REQUEST_REVISION: LifecycleEvent.REQUEST_REVISION,
                DecisionType.CANCEL: LifecycleEvent.CANCEL,
            }[stored_type]
        except (ValueError, KeyError) as exc:
            raise WorkOrderValidationError(
                "request_plan_approval is not a stored decision."
                if decision_type == "request_plan_approval"
                else f"Unknown decision type: {decision_type}."
            ) from exc
        work_order = self._require_work_order(work_order_id)
        try:
            compact_rationale = require_rationale(rationale)
        except LifecycleError as exc:
            raise WorkOrderValidationError(str(exc)) from exc
        decision = StoredDecision(
            decision_id=f"DEC-{uuid4()}",
            decision_type=stored_type,
            work_order_id=work_order_id,
            actor=resolved,
            target_kind=DecisionTargetKind(target_kind),
            target_id=target_id,
            target_sha256=target_sha256.removeprefix("sha256:"),
            permitted_action=permitted_action,
            scope=scope,
            created_at=datetime.now(UTC).isoformat(),
            idempotency_key=idempotency_key,
            rationale=compact_rationale,
            expires_at=expires_at,
        )
        incoming_fingerprint = fingerprint_decision(decision)
        try:
            with self.store.lock_work_order(work_order_id) as session:
                existing = next(
                    (
                        item
                        for item in session.list_decisions()
                        if item["actor"] == resolved
                        and item["decision_type"] == stored_type.value
                        and item["idempotency_key"] == idempotency_key
                    ),
                    None,
                )
                if existing is not None:
                    stored_fingerprint = str(
                        existing.get("fingerprint") or fingerprint_decision(existing)
                    )
                    if stored_fingerprint != incoming_fingerprint:
                        raise IdempotencyConflict(
                            "The idempotency key is already bound to a different decision."
                        )
                    stored_receipt = existing.get("receipt")
                    if isinstance(stored_receipt, dict):
                        return stored_receipt
                    current = session.get_work_order()
                    return {
                        "receipt_type": "decision.recorded",
                        "work_order_id": work_order_id,
                        "decision_id": existing["decision_id"],
                        "decision_type": stored_type.value,
                        "status": current.status.value,
                        "duplicate": True,
                    }
                snapshot = self._snapshot_from_session(session, work_order)
                result = apply_decision(
                    status=session.get_work_order().status,
                    snapshot=snapshot,
                    event=event,
                    decision=decision,
                    expected_version=expected_version,
                )
                session.update_status(result.status)
                entry = session.append_ledger(
                    event_type=result.ledger_event,
                    actor=resolved,
                    counterparty="broker:awr",
                    payload=decision.to_dict(),
                )
                session.put_lifecycle(result.snapshot.to_dict())
                receipt = {
                    "receipt_type": "decision.recorded",
                    "work_order_id": work_order_id,
                    "decision_id": decision.decision_id,
                    "decision_type": stored_type.value,
                    "status": result.status.value,
                    "ledger_sequence": entry.sequence,
                    "fingerprint": incoming_fingerprint,
                }
                session.put_decision(
                    {
                        **decision.to_dict(),
                        "fingerprint": incoming_fingerprint,
                        "receipt": receipt,
                    }
                )
                return receipt
        except LifecycleError as exc:
            raise WorkOrderValidationError(str(exc)) from exc

    def request_plan_approval(
        self, work_order_id: str, *, actor: str | None = None
    ) -> dict[str, Any]:
        return self._apply_broker(
            work_order_id,
            LifecycleEvent.PLAN_APPROVAL_REQUESTED,
            actor=actor,
            message_id=f"APR-{uuid4()}",
        )

    def dispatch_execution(
        self,
        work_order_id: str,
        *,
        actor: str | None = None,
        plan_id: str | None = None,
        plan_sha256: str | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        return self._commit_execution_intent(
            work_order_id,
            event=LifecycleEvent.PLAN_EXECUTE,
            actor=actor,
            plan_id=plan_id,
            plan_sha256=plan_sha256,
            expected_version=expected_version,
        )

    def request_completion_review(
        self, work_order_id: str, *, actor: str | None = None
    ) -> dict[str, Any]:
        return self._apply_broker(
            work_order_id,
            LifecycleEvent.COMPLETION_REVIEW,
            actor=actor,
            message_id=f"CRV-{uuid4()}",
        )

    def answer_question(self, work_order_id: str, *, actor: str | None = None) -> dict[str, Any]:
        return self._apply_broker(
            work_order_id,
            LifecycleEvent.QUESTION_ANSWER,
            actor=actor,
            message_id=f"ANS-{uuid4()}",
        )

    def refine_implementation(
        self,
        work_order_id: str,
        *,
        actor: str | None = None,
        plan_id: str | None = None,
        plan_sha256: str | None = None,
    ) -> dict[str, Any]:
        return self._commit_execution_intent(
            work_order_id,
            event=LifecycleEvent.IMPLEMENTATION_REFINE,
            actor=actor,
            plan_id=plan_id,
            plan_sha256=plan_sha256,
        )

    def refresh_external_run(
        self, work_order_id: str, *, actor: str | None = None
    ) -> dict[str, Any]:
        resolved = self._actor(actor)
        work_order = self._require_work_order(work_order_id)
        if self.get_work_order_artifacts(work_order_id, actor=resolved):
            raise WorkOrderValidationError("DELIVERY_UNSUPPORTED")
        status = work_order.status
        if status is WorkStatus.READY_FOR_EXECUTION:
            intent = self._commit_execution_intent(work_order_id, actor=resolved)
            return self._advance_execution_dispatch(str(intent["dispatch_id"]), actor=resolved)
        if status is WorkStatus.EXECUTION_DISPATCHED:
            dispatch = self._latest_dispatch(work_order_id)
            if dispatch is None:
                raise WorkOrderValidationError("No durable execution dispatch exists.")
            return self._advance_execution_dispatch(str(dispatch["dispatch_id"]), actor=resolved)
        if status is WorkStatus.EXECUTING:
            return self._reconcile_execution_run(work_order_id, actor=resolved)
        if status in {
            WorkStatus.COMPLETION_READY,
            WorkStatus.PLANNER_REVIEWING,
            WorkStatus.WAITING_FOR_HUMAN_REVIEW,
            WorkStatus.COMPLETE,
            WorkStatus.FAILED,
            WorkStatus.CANCELLED,
        }:
            return self._terminal_execution_receipt(work_order_id)
        raise WorkOrderValidationError(
            f"Work order {work_order_id} is not eligible for execution refresh."
        )

    def _apply_broker(
        self,
        work_order_id: str,
        event: LifecycleEvent,
        *,
        actor: str | None,
        message_id: str,
        plan_id: str | None = None,
        plan_sha256: str | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        resolved = self._actor(actor)
        work_order = self._require_work_order(work_order_id)
        try:
            with self.store.lock_work_order(work_order_id) as session:
                snapshot = self._snapshot_from_session(session, work_order)
                decisions = tuple(
                    StoredDecision.from_dict(item) for item in session.list_decisions()
                )
                result = apply_broker_event(
                    status=session.get_work_order().status,
                    snapshot=snapshot,
                    event=event,
                    actor=resolved,
                    message_id=message_id,
                    decisions=decisions,
                    expected_version=expected_version,
                    plan_id=plan_id,
                    plan_sha256=plan_sha256.removeprefix("sha256:") if plan_sha256 else None,
                )
                session.update_status(result.status)
                entry = session.append_ledger(
                    event_type=result.ledger_event,
                    actor=resolved,
                    counterparty="broker:awr",
                    payload={"message_id": message_id},
                )
                session.put_lifecycle(result.snapshot.to_dict())
                return {
                    "receipt_type": event.value,
                    "work_order_id": work_order_id,
                    "status": result.status.value,
                    "ledger_sequence": entry.sequence,
                    "message_id": message_id,
                }
        except LifecycleError as exc:
            raise WorkOrderValidationError(str(exc)) from exc

    def _commit_execution_intent(
        self,
        work_order_id: str,
        *,
        event: LifecycleEvent = LifecycleEvent.PLAN_EXECUTE,
        actor: str | None = None,
        plan_id: str | None = None,
        plan_sha256: str | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        resolved = self._actor(actor)
        work_order = self._require_work_order(work_order_id)
        if self.get_work_order_artifacts(work_order_id, actor=resolved):
            raise WorkOrderValidationError("DELIVERY_UNSUPPORTED")
        now = datetime.now(UTC).isoformat()
        try:
            with self.store.lock_work_order(work_order_id) as session:
                snapshot = self._snapshot_from_session(session, work_order)
                decisions = tuple(
                    StoredDecision.from_dict(item) for item in session.list_decisions()
                )
                existing = session.list_execution_dispatches()
                pending = next(
                    (
                        item
                        for item in reversed(existing)
                        if item["state"]
                        not in {
                            DispatchState.ACKNOWLEDGED.value,
                            DispatchState.FAILED.value,
                        }
                    ),
                    None,
                )
                if (
                    pending is not None
                    and session.get_work_order().status is WorkStatus.EXECUTION_DISPATCHED
                ):
                    log_event(
                        _LOGGER,
                        "execution.duplicate_dispatch_prevented",
                        work_order_id=work_order_id,
                        dispatch_id=pending["dispatch_id"],
                    )
                    receipt = pending.get("receipt")
                    if isinstance(receipt, dict):
                        return {**receipt, "duplicate": True}
                    return {
                        "receipt_type": "plan.execute",
                        "work_order_id": work_order_id,
                        "status": session.get_work_order().status.value,
                        "dispatch_id": pending["dispatch_id"],
                        "duplicate": True,
                    }
                resolved_plan_id = plan_id or snapshot.plan_id
                resolved_plan_sha = (
                    plan_sha256.removeprefix("sha256:") if plan_sha256 else snapshot.plan_sha256
                )
                if not resolved_plan_id or not resolved_plan_sha:
                    raise WorkOrderValidationError("Execution requires a stored plan fingerprint.")
                attempt = max((int(item["attempt"]) for item in existing), default=0) + 1
                message_id = f"EXD-{uuid4()}"
                result = apply_broker_event(
                    status=session.get_work_order().status,
                    snapshot=snapshot,
                    event=event,
                    actor=resolved,
                    message_id=message_id,
                    decisions=decisions,
                    expected_version=expected_version,
                    plan_id=resolved_plan_id,
                    plan_sha256=resolved_plan_sha,
                )
                approval = matching_plan_approval(
                    decisions,
                    plan_id=result.snapshot.plan_id or resolved_plan_id,
                    plan_sha256=result.snapshot.plan_sha256 or resolved_plan_sha,
                )
                plan_content = self._plan_text_from_session(session, work_order)
                wrapped = wrap_execution(
                    work_order_id=work_order_id,
                    plan_id=resolved_plan_id,
                    plan_sha256=resolved_plan_sha,
                    plan_content=plan_content,
                    repository_url=work_order.repository_url,
                    base_ref=work_order.base_ref,
                    attempt=attempt,
                )
                provider_key = replay_cache_key(
                    sender=work_order.sender,
                    recipient=work_order.recipient,
                    directive=f"plan.execute:{attempt}",
                    parent=work_order_id,
                    repository_url=work_order.repository_url,
                    base_ref=work_order.base_ref,
                    content_sha256=resolved_plan_sha,
                    bundle_sha256=wrapped.wrapper_sha256,
                )
                planning_ack = self._latest_from(session.list_ledger(), "executor.acknowledged")
                existing_agent = None
                if planning_ack is not None:
                    raw_agent = planning_ack.payload.get("executor_agent_id")
                    existing_agent = str(raw_agent) if raw_agent else None
                session.update_status(result.status)
                entry = session.append_ledger(
                    event_type=result.ledger_event,
                    actor=resolved,
                    counterparty="broker:awr",
                    payload={
                        "message_id": message_id,
                        "dispatch_id": message_id,
                        "attempt": attempt,
                    },
                )
                session.put_lifecycle(result.snapshot.to_dict())
                receipt = {
                    "receipt_type": event.value,
                    "work_order_id": work_order_id,
                    "status": result.status.value,
                    "ledger_sequence": entry.sequence,
                    "message_id": message_id,
                    "dispatch_id": message_id,
                    "attempt": attempt,
                }
                session.put_execution_dispatch(
                    {
                        "dispatch_id": message_id,
                        "work_order_id": work_order_id,
                        "attempt": attempt,
                        "plan_id": resolved_plan_id,
                        "plan_sha256": resolved_plan_sha,
                        "approval_decision_id": approval.decision_id,
                        "executor": self.executor.name,
                        "repository_url": work_order.repository_url,
                        "base_ref": work_order.base_ref,
                        "wrapper_id": wrapped.wrapper_id,
                        "wrapper_version": wrapped.wrapper_version,
                        "wrapper_sha256": wrapped.wrapper_sha256,
                        "provider_idempotency_key": provider_key,
                        "state": DispatchState.PENDING.value,
                        "attempt_count": 0,
                        "wrapped_markdown": wrapped.markdown,
                        "existing_agent_id": existing_agent,
                        "receipt": receipt,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                log_event(
                    _LOGGER,
                    "execution.intent_created",
                    work_order_id=work_order_id,
                    dispatch_id=message_id,
                    attempt=attempt,
                )
                return receipt
        except LifecycleError as exc:
            raise WorkOrderValidationError(str(exc)) from exc

    def _advance_execution_dispatch(self, dispatch_id: str, *, actor: str) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        current = self.store.get_execution_dispatch(dispatch_id)
        if current is None:
            raise WorkOrderValidationError(f"Unknown execution dispatch: {dispatch_id}")
        if current["state"] == DispatchState.ACKNOWLEDGED.value:
            receipt = current.get("receipt")
            if isinstance(receipt, dict):
                return {**receipt, "duplicate": True}
        claimed = self.store.claim_execution_lease(
            dispatch_id, owner=actor, now=now, ttl_seconds=_LEASE_TTL_SECONDS
        )
        if claimed is None:
            latest = self.store.get_execution_dispatch(dispatch_id)
            if latest and latest.get("receipt"):
                stored = latest["receipt"]
                if isinstance(stored, dict):
                    return {**stored, "duplicate": True}
            raise WorkOrderValidationError("Another worker holds the execution dispatch lease.")
        log_event(
            _LOGGER,
            "execution.lease_claimed",
            dispatch_id=dispatch_id,
            attempt=claimed.get("attempt"),
            attempt_count=claimed.get("attempt_count"),
        )
        if claimed["state"] == DispatchState.ACKNOWLEDGED.value:
            receipt = claimed.get("receipt")
            if isinstance(receipt, dict):
                return {**receipt, "duplicate": True}
        if claimed.get("provider_run_id") and claimed["state"] != DispatchState.ACKNOWLEDGED.value:
            return self._persist_execution_ack(claimed, actor=actor)
        executor = self._execution_executor()
        started = datetime.now(UTC)
        packet = self._to_execution_dispatch(claimed)
        attempt_count = int(claimed.get("attempt_count") or 0)
        follow_up_retry = (
            bool(claimed.get("existing_agent_id"))
            and attempt_count > 1
            and not claimed.get("provider_run_id")
        )
        try:
            if claimed["state"] == DispatchState.RECONCILIATION_REQUIRED.value or follow_up_retry:
                recovered = executor.recover_execution_submission(packet)
                if recovered is None:
                    return self._mark_reconciliation(claimed, "RECONCILIATION_REQUIRED")
                acknowledgement = recovered
            else:
                acknowledgement = executor.submit_for_execution(packet)
        except AmbiguousAcceptance:
            return self._mark_reconciliation(claimed, "RECONCILIATION_REQUIRED")
        except TimeoutError:
            recovered = executor.recover_execution_submission(packet)
            if recovered is None:
                return self._mark_reconciliation(claimed, "RECONCILIATION_REQUIRED")
            acknowledgement = recovered
        except WorkOrderValidationError:
            raise
        except Exception as exc:
            message = str(exc)
            lowered = message.lower()
            if any(token in lowered for token in ("bearer ", "api_key", "authorization")):
                message = "Provider submission failed."
            raise WorkOrderValidationError(message[:300]) from exc
        log_event(
            _LOGGER,
            "execution.provider_submitted",
            dispatch_id=dispatch_id,
            latency_ms=int((datetime.now(UTC) - started).total_seconds() * 1000),
        )
        claimed = self._persist_provider_acceptance(claimed, acknowledgement)
        return self._persist_execution_ack(claimed, actor=actor, acknowledgement=acknowledgement)

    def _persist_provider_acceptance(
        self,
        dispatch: dict[str, Any],
        acknowledgement: ExecutionAcknowledgement,
    ) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        work_order_id = str(dispatch["work_order_id"])
        with self.store.lock_work_order(work_order_id) as session:
            current = session.get_execution_dispatch(str(dispatch["dispatch_id"])) or dispatch
            if current.get("state") == DispatchState.ACKNOWLEDGED.value:
                return current
            current.update(
                {
                    "state": DispatchState.PROVIDER_ACCEPTED.value,
                    "provider_agent_id": acknowledgement.executor_agent_id,
                    "provider_run_id": acknowledgement.executor_run_id,
                    "updated_at": now,
                }
            )
            session.update_execution_dispatch(current)
            return current

    def _persist_execution_ack(
        self,
        dispatch: dict[str, Any],
        *,
        actor: str,
        acknowledgement: ExecutionAcknowledgement | None = None,
    ) -> dict[str, Any]:
        work_order = self._require_work_order(str(dispatch["work_order_id"]))
        run_id = str(dispatch["provider_run_id"])
        agent_id = str(dispatch["provider_agent_id"])
        packet = self._trusted_response(
            work_order,
            ResponseType.EXECUTION_ACKNOWLEDGED,
            payload={
                "executor": self.executor.name,
                "executor_run_id": run_id,
            },
            actor=work_order.recipient,
            idempotency_key=f"exec-ack-{dispatch['dispatch_id']}",
            executor_run_id=run_id,
        )
        receipt = self.submit_response(
            markdown=render_response_markdown(packet), actor=work_order.recipient
        )
        now = datetime.now(UTC).isoformat()
        with self.store.lock_work_order(work_order.work_order_id) as session:
            current = session.get_execution_dispatch(str(dispatch["dispatch_id"])) or dispatch
            current.update(
                {
                    "state": DispatchState.ACKNOWLEDGED.value,
                    "provider_agent_id": agent_id,
                    "provider_run_id": run_id,
                    "receipt": receipt,
                    "updated_at": now,
                    "lease_owner": None,
                    "lease_expires_at": None,
                }
            )
            session.update_execution_dispatch(current)
        log_event(
            _LOGGER,
            "execution.acknowledged",
            work_order_id=work_order.work_order_id,
            dispatch_id=dispatch["dispatch_id"],
        )
        del acknowledgement
        return receipt

    def _reconcile_execution_run(self, work_order_id: str, *, actor: str) -> dict[str, Any]:
        del actor
        work_order = self._require_work_order(work_order_id)
        dispatch = self._latest_dispatch(work_order_id)
        if dispatch is None or not dispatch.get("provider_run_id"):
            raise WorkOrderValidationError("No acknowledged execution run to reconcile.")
        executor = self._execution_executor()
        run = executor.get_execution_run(
            str(dispatch["provider_agent_id"]), str(dispatch["provider_run_id"])
        )
        log_event(
            _LOGGER,
            "execution.provider_status",
            work_order_id=work_order_id,
            status=run.status.value,
        )
        if not run.status.terminal:
            return self._ingest_progress(work_order, run)
        if run.status is ExecutorRunStatus.FINISHED:
            receipt = self._ingest_terminal(work_order, run)
            return self._remember_dispatch_receipt(work_order.work_order_id, receipt)
        receipt = self._ingest_failure(work_order, run)
        return self._remember_dispatch_receipt(work_order.work_order_id, receipt)

    def _ingest_progress(self, work_order: WorkOrder, run: ExecutionRunResult) -> dict[str, Any]:
        packet = self._trusted_response(
            work_order,
            ResponseType.EXECUTION_PROGRESS,
            payload={"message": "Execution is running.", "percent": 40},
            actor=work_order.recipient,
            idempotency_key=f"exec-progress-{run.executor_run_id}",
            executor_run_id=run.executor_run_id,
        )
        return self.submit_response(
            markdown=render_response_markdown(packet), actor=work_order.recipient
        )

    def _remember_dispatch_receipt(
        self, work_order_id: str, receipt: dict[str, Any]
    ) -> dict[str, Any]:
        dispatch = self._latest_dispatch(work_order_id)
        if dispatch is None:
            return receipt
        now = datetime.now(UTC).isoformat()
        with self.store.lock_work_order(work_order_id) as session:
            current = session.get_execution_dispatch(str(dispatch["dispatch_id"])) or dispatch
            current["receipt"] = receipt
            current["updated_at"] = now
            session.update_execution_dispatch(current)
        return receipt

    def _ingest_terminal(self, work_order: WorkOrder, run: ExecutionRunResult) -> dict[str, Any]:
        text = (run.result or "").strip()
        if text and len(text.encode("utf-8")) > MAX_BODY_BYTES:
            return self._malformed_terminal(work_order, run, "oversized terminal output")
        heading = text.splitlines()[0].strip() if text else ""
        if heading == "@response":
            try:
                parsed = parse_response_markdown(text)
            except (ResponsePacketError, ValueError) as exc:
                return self._malformed_terminal(work_order, run, str(exc))
            if parsed.response_type not in {
                ResponseType.EXECUTION_COMPLETED,
                ResponseType.EXECUTION_FAILED,
                ResponseType.QUESTION_BLOCKED,
            }:
                return self._malformed_terminal(
                    work_order, run, "terminal @response is not a completion packet"
                )
            rebuilt = self._trusted_response(
                work_order,
                parsed.response_type,
                payload=parsed.payload,
                actor=work_order.recipient,
                idempotency_key=parsed.idempotency_key or f"exec-term-{run.executor_run_id}",
                executor_run_id=run.executor_run_id,
            )
            return self.submit_response(
                markdown=render_response_markdown(rebuilt), actor=work_order.recipient
            )
        if text in {"@response-completed", ""} or text.startswith("# Recorded"):
            payload: dict[str, Any] = {"summary": "Implementation completed."}
            if run.git:
                git = run.git
                if isinstance(git.get("repository"), str):
                    payload["repository"] = git["repository"]
                if isinstance(git.get("branch"), str):
                    payload["branch"] = git["branch"]
                if isinstance(git.get("base_ref"), str):
                    payload["base_ref"] = git["base_ref"]
                if isinstance(git.get("commit_sha"), str):
                    payload["commit_sha"] = git["commit_sha"]
                if isinstance(git.get("pull_request_url"), str):
                    payload["pull_request_url"] = git["pull_request_url"]
            if not text and not run.git:
                return self._malformed_terminal(work_order, run, "empty terminal output")
            packet = self._trusted_response(
                work_order,
                ResponseType.EXECUTION_COMPLETED,
                payload=payload,
                actor=work_order.recipient,
                idempotency_key=f"exec-completed-{run.executor_run_id}",
                executor_run_id=run.executor_run_id,
            )
            return self.submit_response(
                markdown=render_response_markdown(packet), actor=work_order.recipient
            )
        return self._malformed_terminal(work_order, run, "unstructured terminal output")

    def _ingest_failure(self, work_order: WorkOrder, run: ExecutionRunResult) -> dict[str, Any]:
        packet = self._trusted_response(
            work_order,
            ResponseType.EXECUTION_FAILED,
            payload={"error_type": run.status.value, "message": "Provider run terminated."},
            actor=work_order.recipient,
            idempotency_key=f"exec-failed-{run.executor_run_id}",
            executor_run_id=run.executor_run_id,
        )
        return self.submit_response(
            markdown=render_response_markdown(packet), actor=work_order.recipient
        )

    def _malformed_terminal(
        self, work_order: WorkOrder, run: ExecutionRunResult, detail: str
    ) -> dict[str, Any]:
        del detail
        log_event(
            _LOGGER,
            "execution.malformed_response",
            work_order_id=work_order.work_order_id,
        )
        packet = self._trusted_response(
            work_order,
            ResponseType.EXECUTION_FAILED,
            payload={
                "error_type": "MALFORMED_EXECUTOR_RESPONSE",
                "message": "Executor returned a malformed terminal response.",
            },
            actor=work_order.recipient,
            idempotency_key=f"exec-malformed-{run.executor_run_id}",
            executor_run_id=run.executor_run_id,
        )
        receipt = self.submit_response(
            markdown=render_response_markdown(packet), actor=work_order.recipient
        )
        return {**receipt, "error": "MALFORMED_EXECUTOR_RESPONSE"}

    def _mark_reconciliation(self, dispatch: dict[str, Any], code: str) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        work_order_id = str(dispatch["work_order_id"])
        with self.store.lock_work_order(work_order_id) as session:
            current = session.get_execution_dispatch(str(dispatch["dispatch_id"])) or dispatch
            current.update(
                {
                    "state": DispatchState.RECONCILIATION_REQUIRED.value,
                    "last_failure_code": code,
                    "updated_at": now,
                    "lease_owner": None,
                    "lease_expires_at": None,
                }
            )
            session.update_execution_dispatch(current)
        log_event(
            _LOGGER,
            "execution.reconciliation_required",
            work_order_id=work_order_id,
            dispatch_id=dispatch["dispatch_id"],
        )
        return {
            "receipt_type": "execution.reconciliation_required",
            "work_order_id": work_order_id,
            "dispatch_id": dispatch["dispatch_id"],
            "status": self._require_work_order(work_order_id).status.value,
            "error": code,
        }

    def _terminal_execution_receipt(self, work_order_id: str) -> dict[str, Any]:
        dispatch = self._latest_dispatch(work_order_id)
        if dispatch and isinstance(dispatch.get("receipt"), dict):
            work_order = self._require_work_order(work_order_id)
            stored = dict(dispatch["receipt"])
            stored["status"] = work_order.status.value
            stored["duplicate"] = True
            return stored
        completed = self._latest_event(work_order_id, "execution.completed")
        work_order = self._require_work_order(work_order_id)
        return {
            "receipt_type": "execution.completed" if completed else "execution.refresh",
            "work_order_id": work_order_id,
            "status": work_order.status.value,
            "duplicate": True,
        }

    def _latest_dispatch(self, work_order_id: str) -> dict[str, Any] | None:
        items = self.store.list_execution_dispatches(work_order_id)
        return items[-1] if items else None

    def _plan_text_from_session(self, session: WorkOrderSession, work_order: WorkOrder) -> str:
        stored = session.get_latest_response("plan.completed")
        if stored is not None:
            packet = stored.get("packet")
            if isinstance(packet, dict):
                payload = packet.get("payload")
                if isinstance(payload, dict) and isinstance(payload.get("content"), str):
                    return str(payload["content"])
        plan = self._plan_from_ledger(work_order.work_order_id)
        if plan is not None:
            return plan.content
        return work_order.markdown

    def _trusted_response(
        self,
        work_order: WorkOrder,
        response_type: ResponseType,
        *,
        payload: dict[str, Any],
        actor: str,
        idempotency_key: str,
        executor_run_id: str | None,
    ) -> ResponsePacket:
        with self.store.lock_work_order(work_order.work_order_id) as session:
            snapshot = self._snapshot_from_session(session, work_order)
            parent = snapshot.current_parent_id
            source = snapshot.source_input_sha256
        raw = {
            "schema": RESPONSE_SCHEMA,
            "response_type": response_type.value,
            "work_order_id": work_order.work_order_id,
            "in_reply_to": parent,
            "idempotency_key": idempotency_key,
            "source_input_sha256": source,
            "created_at": datetime.now(UTC).isoformat(),
            "authority": RESPONSE_AUTHORITY,
            "payload": payload,
            "executor_run_id": executor_run_id,
        }
        del actor
        return parse_response_packet(raw)

    def _to_execution_dispatch(self, record: dict[str, Any]) -> ExecutionDispatch:
        return ExecutionDispatch(
            dispatch_id=str(record["dispatch_id"]),
            work_order_id=str(record["work_order_id"]),
            attempt=int(record["attempt"]),
            plan_id=str(record["plan_id"]),
            plan_sha256=str(record["plan_sha256"]),
            approval_decision_id=str(record["approval_decision_id"]),
            executor=str(record["executor"]),
            repository_url=str(record["repository_url"]),
            base_ref=str(record["base_ref"]),
            wrapper_id=str(record["wrapper_id"]),
            wrapper_version=str(record["wrapper_version"]),
            wrapper_sha256=str(record["wrapper_sha256"]),
            provider_idempotency_key=str(record["provider_idempotency_key"]),
            wrapped_markdown=str(record["wrapped_markdown"]),
            existing_agent_id=(
                str(record["existing_agent_id"]) if record.get("existing_agent_id") else None
            ),
        )

    def _execution_executor(self) -> ExecutionExecutor:
        executor = self.executor
        if not hasattr(executor, "submit_for_execution"):
            raise WorkOrderValidationError("The configured executor does not support execution.")
        return executor  # type: ignore[return-value]

    def _authorize_reader(
        self, actor: str, work_order: WorkOrder, snapshot: LifecycleSnapshot
    ) -> None:
        allowed = snapshot.participants | {work_order.sender, work_order.recipient}
        if actor not in allowed:
            raise WorkOrderValidationError(f"{actor} is not authorized to read this work order.")

    def _authorize_projection(self, work_order_id: str, actor: str | None) -> WorkOrder:
        work_order = self._require_work_order(work_order_id)
        if actor is None:
            return work_order
        resolved = self._actor(actor)
        with self.store.lock_work_order(work_order_id) as session:
            snapshot = self._snapshot_from_session(session, work_order)
            self._authorize_reader(resolved, work_order, snapshot)
        return work_order

    def _snapshot_from_session(
        self, session: WorkOrderSession, work_order: WorkOrder
    ) -> LifecycleSnapshot:
        raw = session.get_lifecycle()
        if raw is not None:
            snapshot = LifecycleSnapshot.from_dict(raw)
            if snapshot.decision_principals and snapshot.executor_principals:
                return snapshot
            return replace(
                snapshot,
                decision_principals=snapshot.decision_principals or frozenset({work_order.sender}),
                executor_principals=snapshot.executor_principals
                or frozenset({work_order.recipient}),
            )
        return derive_snapshot(
            work_order.work_order_id,
            work_order.sender,
            work_order.recipient,
            work_order.content_sha256,
        )

    def _validate_evidence(self, work_order: WorkOrder, packet: ResponsePacket, actor: str) -> None:
        if not packet.evidence_refs:
            return
        artifacts = self._require_artifacts()
        for reference in packet.evidence_refs:
            current = artifacts.metadata.get(reference.artifact_id)
            if current is None:
                raise WorkOrderValidationError(
                    f"Unknown evidence artifact: {reference.artifact_id}"
                )
            if current.status is not ArtifactStatus.CLEAN:
                raise WorkOrderValidationError("Evidence artifacts must be CLEAN.")
            if current.owner not in {work_order.sender, actor}:
                raise WorkOrderValidationError(
                    "Evidence artifact owner does not match the work order."
                )
            if current.sha256 and current.sha256 != reference.sha256:
                raise WorkOrderValidationError("Evidence artifact fingerprint does not match.")

    def _require_artifacts(self) -> ArtifactRelay:
        if self.artifacts is None:
            raise WorkOrderValidationError("Artifact intake is not configured.")
        return self.artifacts

    @staticmethod
    def _actor(explicit: str | None) -> str:
        try:
            return resolve_actor(explicit)
        except ValueError as exc:
            raise WorkOrderValidationError(str(exc)) from exc

    def _record_bundle(self, work_order: WorkOrder, bundle: WorkBundle, actor: str) -> None:
        artifacts = self._require_artifacts()
        with self.store.lock_work_order(work_order.work_order_id) as session:
            if self._latest_from(session.list_ledger(), "bundle.validated") is None:
                session.append_ledger(
                    event_type="bundle.validated",
                    actor="broker:awr",
                    counterparty=actor,
                    payload={
                        "bundle_sha256": bundle.bundle_sha256,
                        "references": reference_payloads(bundle),
                    },
                )
        for reference in bundle.references:
            current = artifacts.metadata.get(reference.artifact_id)
            correlation_id = (
                current.correlation_id if current is not None else work_order.work_order_id
            )
            artifacts.metadata.append_receipt(
                reference.artifact_id,
                "artifact.relay_authorized",
                "broker:awr",
                actor,
                {
                    "work_order_id": work_order.work_order_id,
                    "sha256": reference.sha256,
                    "bundle_sha256": bundle.bundle_sha256,
                    "purpose": reference.purpose.value,
                },
                correlation_id=correlation_id,
                work_order_id=work_order.work_order_id,
            )

    def _route_and_dispatch(
        self,
        *,
        work_order: WorkOrder,
        wrapped: WrappedPrompt,
        parent: WorkOrder | None,
    ) -> SubmissionReceipt:
        routed = self._ensure_routed(work_order)
        try:
            acknowledgement = self.executor.submit_for_planning(
                PlanningDispatch(
                    work_order_id=work_order.work_order_id,
                    recipient=work_order.recipient,
                    mode="PLAN_ONLY",
                    repository_url=work_order.repository_url,
                    base_ref=work_order.base_ref,
                    existing_agent_id=self._parent_agent_id(parent),
                    wrapped_markdown=wrapped.markdown,
                    content_sha256=work_order.content_sha256,
                    wrapper_id=work_order.wrapper_id,
                    wrapper_version=work_order.wrapper_version,
                    wrapper_sha256=work_order.wrapper_sha256,
                )
            )
        except Exception as exc:
            with self.store.lock_work_order(work_order.work_order_id) as session:
                if self._latest_from(session.list_ledger(), "executor.acknowledged") is None:
                    session.update_status(WorkStatus.FAILED)
                    session.append_ledger(
                        event_type="executor.failed",
                        actor=self.executor.name,
                        counterparty="broker:awr",
                        payload={"error_type": type(exc).__name__, "message": str(exc)},
                    )
            raise
        if not acknowledgement.accepted:
            with self.store.lock_work_order(work_order.work_order_id) as session:
                existing_rejected = self._latest_from(session.list_ledger(), "executor.rejected")
                if existing_rejected is None:
                    session.update_status(WorkStatus.FAILED)
                    existing_rejected = session.append_ledger(
                        event_type="executor.rejected",
                        actor=acknowledgement.executor,
                        counterparty="broker:awr",
                        payload={"message": acknowledgement.message},
                    )
            return SubmissionReceipt(
                receipt_type="executor.rejected",
                work_order_id=work_order.work_order_id,
                content_sha256=work_order.content_sha256,
                status=WorkStatus.FAILED,
                duplicate=False,
                executor_run_id=acknowledgement.executor_run_id,
                ledger_sequence=existing_rejected.sequence,
                bundle_sha256=work_order.bundle_sha256,
            )

        with self.store.lock_work_order(work_order.work_order_id) as session:
            existing_ack = self._latest_from(session.list_ledger(), "executor.acknowledged")
            if existing_ack is not None:
                current = session.get_work_order()
                return SubmissionReceipt(
                    receipt_type="executor.acknowledged",
                    work_order_id=work_order.work_order_id,
                    content_sha256=work_order.content_sha256,
                    status=current.status,
                    duplicate=True,
                    executor_run_id=str(existing_ack.payload["executor_run_id"]),
                    ledger_sequence=existing_ack.sequence,
                    bundle_sha256=work_order.bundle_sha256,
                )
            session.update_status(WorkStatus.PLANNING)
            acknowledged = session.append_ledger(
                event_type="executor.acknowledged",
                actor=acknowledgement.executor,
                counterparty="broker:awr",
                payload={
                    "executor_agent_id": acknowledgement.executor_agent_id,
                    "executor_run_id": acknowledgement.executor_run_id,
                    "executor_url": acknowledgement.executor_url,
                    "message": acknowledgement.message,
                    "routed_sequence": routed.sequence,
                },
            )
        return SubmissionReceipt(
            receipt_type="executor.acknowledged",
            work_order_id=work_order.work_order_id,
            content_sha256=work_order.content_sha256,
            status=WorkStatus.PLANNING,
            duplicate=False,
            executor_run_id=acknowledgement.executor_run_id,
            ledger_sequence=acknowledged.sequence,
            bundle_sha256=work_order.bundle_sha256,
        )

    def _ensure_routed(self, work_order: WorkOrder) -> LedgerEntry:
        with self.store.lock_work_order(work_order.work_order_id) as session:
            existing = self._latest_from(session.list_ledger(), "work_order.routed")
            current = session.get_work_order()
            if existing is not None:
                if current.status in {WorkStatus.ACCEPTED, WorkStatus.FAILED}:
                    session.update_status(WorkStatus.ROUTED)
                return existing
            routed = session.append_ledger(
                event_type="work_order.routed",
                actor="broker:awr",
                counterparty=work_order.recipient,
                payload={
                    "mode": "PLAN_ONLY",
                    "executor": self.executor.name,
                    "repository_url": work_order.repository_url,
                    "base_ref": work_order.base_ref,
                },
            )
            session.update_status(WorkStatus.ROUTED)
            return routed

    def _resolve_parent(self, parent_work_order_id: str | None) -> WorkOrder | None:
        if parent_work_order_id is None:
            return None
        parent = self.store.get_work_order(parent_work_order_id)
        if parent is None:
            raise WorkOrderValidationError(f"Unknown parent work order: {parent_work_order_id}")
        return parent

    def _resolve_repository(
        self,
        *,
        repository_url: str | None,
        base_ref: str | None,
        parent: WorkOrder | None,
    ) -> tuple[str, str]:
        if parent is not None:
            if (
                repository_url is not None
                and self._normalize_repository(repository_url) != parent.repository_url
            ):
                raise WorkOrderValidationError(
                    "A refinement may not change the parent work order repository."
                )
            if base_ref is not None and base_ref != parent.base_ref:
                raise WorkOrderValidationError(
                    "A refinement may not change the parent work order base reference."
                )
            return parent.repository_url, parent.base_ref

        configured_repository = repository_url or self.default_repository_url
        if configured_repository is None:
            raise WorkOrderValidationError(
                "repository_url is required for a new feature work order."
            )
        resolved_base_ref = base_ref or self.default_base_ref
        if not resolved_base_ref.strip() or any(char.isspace() for char in resolved_base_ref):
            raise WorkOrderValidationError("base_ref must be a non-empty Git reference.")
        return self._normalize_repository(configured_repository), resolved_base_ref

    @staticmethod
    def _reject_oversized_markdown(markdown: str) -> None:
        if len(markdown.encode("utf-8")) > MAX_MARKDOWN_BYTES:
            raise WorkOrderValidationError("Markdown payload exceeds the 512 KiB limit.")

    @staticmethod
    def _normalize_repository(repository_url: str) -> str:
        parsed = urlparse(repository_url.strip())
        parts = [part for part in parsed.path.split("/") if part]
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or len(parts) != 2
        ):
            raise WorkOrderValidationError("repository_url must be an HTTPS GitHub repository URL.")
        return f"https://github.com/{parts[0]}/{parts[1].removesuffix('.git')}"

    def _parent_agent_id(self, parent: WorkOrder | None) -> str | None:
        if parent is None:
            return None
        acknowledgement = self._latest_event(parent.work_order_id, "executor.acknowledged")
        if acknowledgement is None:
            raise WorkOrderValidationError(
                "The parent work order has no durable executor session to refine."
            )
        return str(acknowledgement.payload["executor_agent_id"])

    def _require_work_order(self, work_order_id: str) -> WorkOrder:
        work_order = self.store.get_work_order(work_order_id)
        if work_order is None:
            raise WorkOrderValidationError(f"Unknown work order: {work_order_id}")
        return work_order

    def _latest_event(self, work_order_id: str, event_type: str) -> LedgerEntry | None:
        return self._latest_from(self.store.list_ledger(work_order_id), event_type)

    @staticmethod
    def _latest_from(entries: list[LedgerEntry], event_type: str) -> LedgerEntry | None:
        matches = [entry for entry in entries if entry.event_type == event_type]
        return matches[-1] if matches else None

    def _plan_from_ledger(self, work_order_id: str) -> PlanPacket | None:
        return self._plan_from_entries(work_order_id, self.store.list_ledger(work_order_id))

    def _plan_from_entries(
        self, work_order_id: str, entries: list[LedgerEntry]
    ) -> PlanPacket | None:
        received = self._latest_from(entries, "plan.received")
        available = self._latest_from(entries, "plan.available")
        if received is None or available is None:
            return None
        return self._plan_packet(work_order_id, received, available)

    def _commit_existing_plan(self, session: WorkOrderSession) -> PlanPacket | None:
        work_order = session.get_work_order()
        entries = session.list_ledger()
        complete = self._plan_from_entries(work_order.work_order_id, entries)
        if complete is not None:
            return complete
        received = self._latest_from(entries, "plan.received")
        if received is None:
            return None
        if work_order.status is not WorkStatus.PLAN_READY:
            session.update_status(WorkStatus.PLAN_READY)
        available = session.append_ledger(
            event_type="plan.available",
            actor="broker:awr",
            counterparty=work_order.sender,
            payload={
                "plan_id": str(received.payload["plan_id"]),
                "content_sha256": str(received.payload["content_sha256"]),
                "received_sequence": received.sequence,
            },
        )
        return self._plan_packet(work_order.work_order_id, received, available)

    @staticmethod
    def _plan_packet(
        work_order_id: str,
        received: LedgerEntry,
        available: LedgerEntry,
    ) -> PlanPacket:
        payload = received.payload
        git = payload.get("git")
        duration = payload.get("duration_ms")
        return PlanPacket(
            plan_id=str(payload["plan_id"]),
            work_order_id=work_order_id,
            executor=received.actor,
            executor_agent_id=str(payload["executor_agent_id"]),
            executor_run_id=str(payload["executor_run_id"]),
            content=str(payload["content"]),
            content_sha256=str(payload["content_sha256"]),
            duration_ms=duration if isinstance(duration, int) else None,
            git=git if isinstance(git, dict) else None,
            completed_at=received.created_at,
            ledger_sequence=available.sequence,
        )

    @staticmethod
    def _validate_replay(candidate: WorkOrder, existing: WorkOrder) -> None:
        comparable_candidate = (
            candidate.sender,
            candidate.recipient,
            candidate.parent_work_order_id,
            candidate.repository_url,
            candidate.base_ref,
            candidate.content_sha256,
            candidate.bundle_sha256,
        )
        comparable_existing = (
            existing.sender,
            existing.recipient,
            existing.parent_work_order_id,
            existing.repository_url,
            existing.base_ref,
            existing.content_sha256,
            existing.bundle_sha256,
        )
        if comparable_candidate != comparable_existing:
            raise WorkOrderValidationError(
                "The idempotency key is already bound to a different work order payload."
            )

    @staticmethod
    def _derive_idempotency_key(
        *,
        sender: str,
        recipient: str,
        directive: str,
        parent: str | None,
        repository_url: str,
        base_ref: str,
        content_sha256: str,
        bundle_sha256: str = "",
    ) -> str:
        return replay_cache_key(
            sender=sender,
            recipient=recipient,
            directive=directive,
            parent=parent,
            repository_url=repository_url,
            base_ref=base_ref,
            content_sha256=content_sha256,
            bundle_sha256=bundle_sha256,
        )
