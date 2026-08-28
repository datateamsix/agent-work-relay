from __future__ import annotations

from typing import Any, Protocol

from ..contracts import LedgerEntry, WorkOrder, WorkStatus


class StateStore(Protocol):
    def create_work_order(self, work_order: WorkOrder) -> tuple[WorkOrder, bool, int]: ...

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
