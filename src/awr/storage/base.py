from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol

from ..contracts import LedgerEntry, WorkOrder, WorkStatus


class WorkOrderSession(Protocol):
    """A single-work-order write transaction used for idempotent state updates."""

    def get_work_order(self) -> WorkOrder: ...

    def list_ledger(self) -> list[LedgerEntry]: ...

    def update_status(self, status: WorkStatus) -> None: ...

    def append_ledger(
        self,
        event_type: str,
        actor: str,
        counterparty: str,
        payload: dict[str, Any],
    ) -> LedgerEntry: ...

    def put_response_packet(self, packet: dict[str, Any]) -> None: ...

    def get_response_by_idempotency(
        self, actor: str, response_type: str, idempotency_key: str
    ) -> dict[str, Any] | None: ...

    def put_decision(self, decision: dict[str, Any]) -> None: ...

    def list_decisions(self) -> list[dict[str, Any]]: ...

    def put_lifecycle(self, snapshot: dict[str, Any]) -> None: ...

    def get_lifecycle(self) -> dict[str, Any] | None: ...


class StateStore(Protocol):
    def create_work_order(self, work_order: WorkOrder) -> tuple[WorkOrder, bool, int]: ...

    def get_work_order(self, work_order_id: str) -> WorkOrder | None: ...

    def get_by_idempotency_key(self, idempotency_key: str) -> WorkOrder | None: ...

    def update_status(self, work_order_id: str, status: WorkStatus) -> None: ...

    def append_ledger(
        self,
        work_order_id: str,
        event_type: str,
        actor: str,
        counterparty: str,
        payload: dict[str, Any],
    ) -> LedgerEntry: ...

    def list_ledger(self, work_order_id: str | None = None) -> list[LedgerEntry]: ...

    def list_work_orders(self) -> list[WorkOrder]: ...

    def lock_work_order(self, work_order_id: str) -> AbstractContextManager[WorkOrderSession]: ...
