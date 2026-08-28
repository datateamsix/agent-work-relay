from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

from .contracts import PlanningDispatch, SubmissionReceipt, WorkOrder, WorkStatus
from .decorators import parse_directive
from .executors.base import PlanningExecutor
from .storage.base import StateStore
from .wrappers import wrap_prompt


class BrokerService:
    def __init__(self, store: StateStore, executor: PlanningExecutor) -> None:
        self.store = store
        self.executor = executor

    def submit_prompt_for_planning(
        self,
        *,
        markdown: str,
        sender: str,
        recipient: str,
        idempotency_key: str | None = None,
    ) -> SubmissionReceipt:
        directive = parse_directive(markdown)
        content_sha256 = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        replay_key = idempotency_key or self._derive_idempotency_key(
            sender=sender,
            recipient=recipient,
            directive=directive.name,
            parent=directive.parent_work_order_id,
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
            payload={"mode": "PLAN_ONLY", "executor": self.executor.name},
        )
        self.store.update_status(work_order.work_order_id, WorkStatus.ROUTED)

        try:
            acknowledgement = self.executor.submit_for_planning(
                PlanningDispatch(
                    work_order_id=work_order.work_order_id,
                    recipient=recipient,
                    mode="PLAN_ONLY",
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
                "executor_run_id": acknowledgement.executor_run_id,
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

    @staticmethod
    def _derive_idempotency_key(
        *, sender: str, recipient: str, directive: str, parent: str | None, content_sha256: str
    ) -> str:
        canonical = "|".join((sender, recipient, directive, parent or "", content_sha256))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
