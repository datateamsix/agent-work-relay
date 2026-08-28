from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

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
from .storage.base import StateStore
from .wrappers import wrap_prompt


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
    ) -> None:
        self.store = store
        self.executor = executor
        self.default_repository_url = default_repository_url
        self.default_base_ref = default_base_ref

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
        )
        work_order_id = f"EWB-{uuid4()}"
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
            acknowledged_events = [
                entry
                for entry in self.store.list_ledger(work_order.work_order_id)
                if entry.event_type == "executor.acknowledged"
            ]
            executor_run_id = (
                str(acknowledged_events[-1].payload["executor_run_id"])
                if acknowledged_events
                else None
            )
            return SubmissionReceipt(
                receipt_type="work_order.accepted",
                work_order_id=work_order.work_order_id,
                content_sha256=work_order.content_sha256,
                status=work_order.status,
                duplicate=True,
                executor_run_id=executor_run_id,
                ledger_sequence=ledger_sequence,
            )

        routed = self.store.append_ledger(
            work_order_id=work_order.work_order_id,
            event_type="work_order.routed",
            actor="broker:ewb",
            counterparty=recipient,
            payload={
                "mode": "PLAN_ONLY",
                "executor": self.executor.name,
                "repository_url": resolved_repository_url,
                "base_ref": resolved_base_ref,
            },
        )
        self.store.update_status(work_order.work_order_id, WorkStatus.ROUTED)

        try:
            acknowledgement = self.executor.submit_for_planning(
                PlanningDispatch(
                    work_order_id=work_order.work_order_id,
                    recipient=recipient,
                    mode="PLAN_ONLY",
                    repository_url=resolved_repository_url,
                    base_ref=resolved_base_ref,
                    existing_agent_id=self._parent_agent_id(parent),
                    wrapped_markdown=wrapped.markdown,
                    content_sha256=content_sha256,
                    wrapper_id=wrapped.wrapper_id,
                    wrapper_version=wrapped.wrapper_version,
                    wrapper_sha256=wrapped.wrapper_sha256,
                )
            )
        except Exception as exc:
            self.store.update_status(work_order.work_order_id, WorkStatus.FAILED)
            self.store.append_ledger(
                work_order_id=work_order.work_order_id,
                event_type="executor.failed",
                actor=self.executor.name,
                counterparty="broker:ewb",
                payload={"error_type": type(exc).__name__, "message": str(exc)},
            )
            raise
        if not acknowledgement.accepted:
            self.store.update_status(work_order.work_order_id, WorkStatus.FAILED)
            failed = self.store.append_ledger(
                work_order_id=work_order.work_order_id,
                event_type="executor.rejected",
                actor=acknowledgement.executor,
                counterparty="broker:ewb",
                payload={"message": acknowledgement.message},
            )
            return SubmissionReceipt(
                receipt_type="executor.rejected",
                work_order_id=work_order.work_order_id,
                content_sha256=content_sha256,
                status=WorkStatus.FAILED,
                duplicate=False,
                executor_run_id=acknowledgement.executor_run_id,
                ledger_sequence=failed.sequence,
            )

        self.store.update_status(work_order.work_order_id, WorkStatus.PLANNING)
        acknowledged = self.store.append_ledger(
            work_order_id=work_order.work_order_id,
            event_type="executor.acknowledged",
            actor=acknowledgement.executor,
            counterparty="broker:ewb",
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
            content_sha256=content_sha256,
            status=WorkStatus.PLANNING,
            duplicate=False,
            executor_run_id=acknowledgement.executor_run_id,
            ledger_sequence=acknowledged.sequence,
        )

    def refresh_planning(self, work_order_id: str) -> PlanningStatusReceipt | PlanPacket:
        work_order = self._require_work_order(work_order_id)
        existing_plan = self._plan_from_ledger(work_order_id)
        if existing_plan is not None:
            return existing_plan

        acknowledgement = self._latest_event(work_order_id, "executor.acknowledged")
        if acknowledgement is None:
            raise WorkOrderValidationError(
                f"Work order {work_order_id} has no executor acknowledgement."
            )
        executor_agent_id = str(acknowledgement.payload["executor_agent_id"])
        executor_run_id = str(acknowledgement.payload["executor_run_id"])
        run = self.executor.get_planning_run(executor_agent_id, executor_run_id)

        if not run.status.terminal:
            status_event = self._latest_event(work_order_id, "executor.status")
            ledger_sequence = acknowledgement.sequence
            if status_event is None or status_event.payload.get("status") != run.status.value:
                status_event = self.store.append_ledger(
                    work_order_id=work_order_id,
                    event_type="executor.status",
                    actor=run.executor,
                    counterparty="broker:ewb",
                    payload={
                        "executor_agent_id": executor_agent_id,
                        "executor_run_id": executor_run_id,
                        "status": run.status.value,
                    },
                )
            ledger_sequence = status_event.sequence
            return PlanningStatusReceipt(
                work_order_id=work_order_id,
                status=work_order.status,
                executor_status=run.status,
                executor_agent_id=executor_agent_id,
                executor_run_id=executor_run_id,
                ledger_sequence=ledger_sequence,
            )

        if run.status is not ExecutorRunStatus.FINISHED or run.result is None:
            failed = self._latest_event(work_order_id, "executor.failed")
            if failed is None:
                self.store.update_status(work_order_id, WorkStatus.FAILED)
                failed = self.store.append_ledger(
                    work_order_id=work_order_id,
                    event_type="executor.failed",
                    actor=run.executor,
                    counterparty="broker:ewb",
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
        received = self.store.append_ledger(
            work_order_id=work_order_id,
            event_type="plan.received",
            actor=run.executor,
            counterparty="broker:ewb",
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
        self.store.update_status(work_order_id, WorkStatus.PLAN_READY)
        available = self.store.append_ledger(
            work_order_id=work_order_id,
            event_type="plan.available",
            actor="broker:ewb",
            counterparty=work_order.sender,
            payload={
                "plan_id": plan_id,
                "content_sha256": content_sha256,
                "received_sequence": received.sequence,
            },
        )
        return PlanPacket(
            plan_id=plan_id,
            work_order_id=work_order_id,
            executor=run.executor,
            executor_agent_id=executor_agent_id,
            executor_run_id=executor_run_id,
            content=run.result,
            content_sha256=content_sha256,
            duration_ms=run.duration_ms,
            git=run.git,
            completed_at=received.created_at,
            ledger_sequence=available.sequence,
        )

    def get_plan(self, work_order_id: str) -> PlanPacket:
        self._require_work_order(work_order_id)
        plan = self._plan_from_ledger(work_order_id)
        if plan is None:
            raise WorkOrderValidationError(f"Plan is not ready for work order {work_order_id}.")
        return plan

    def get_work_order_timeline(self, work_order_id: str) -> list[dict[str, Any]]:
        self._require_work_order(work_order_id)
        return [entry.to_dict() for entry in self.store.list_ledger(work_order_id)]

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
        matches = [
            entry
            for entry in self.store.list_ledger(work_order_id)
            if entry.event_type == event_type
        ]
        return matches[-1] if matches else None

    def _plan_from_ledger(self, work_order_id: str) -> PlanPacket | None:
        received = self._latest_event(work_order_id, "plan.received")
        if received is None:
            return None
        available = self._latest_event(work_order_id, "plan.available")
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
            ledger_sequence=available.sequence if available is not None else received.sequence,
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
        )
        comparable_existing = (
            existing.sender,
            existing.recipient,
            existing.parent_work_order_id,
            existing.repository_url,
            existing.base_ref,
            existing.content_sha256,
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
    ) -> str:
        canonical = "|".join(
            (
                sender,
                recipient,
                directive,
                parent or "",
                repository_url,
                base_ref,
                content_sha256,
            )
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
