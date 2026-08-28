from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class WorkKind(StrEnum):
    FEATURE = "feature"
    REFINEMENT = "refinement"


class WorkAction(StrEnum):
    PLAN = "plan"


class WorkStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    ROUTED = "ROUTED"
    PLANNING = "PLANNING"
    PLAN_READY = "PLAN_READY"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class Directive:
    kind: WorkKind
    action: WorkAction
    parent_work_order_id: str | None = None

    @property
    def name(self) -> str:
        return f"{self.kind.value}.{self.action.value}"


@dataclass(frozen=True, slots=True)
class WorkOrder:
    work_order_id: str
    idempotency_key: str
    sender: str
    recipient: str
    kind: WorkKind
    action: WorkAction
    parent_work_order_id: str | None
    markdown: str
    content_sha256: str
    wrapper_id: str
    wrapper_version: str
    wrapper_sha256: str
    status: WorkStatus
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PlanningDispatch:
    work_order_id: str
    recipient: str
    mode: str
    wrapped_markdown: str
    content_sha256: str
    wrapper_id: str
    wrapper_version: str
    wrapper_sha256: str


@dataclass(frozen=True, slots=True)
class ExecutorAcknowledgement:
    executor_run_id: str
    executor: str
    accepted: bool
    message: str


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    sequence: int
    event_id: str
    work_order_id: str
    event_type: str
    actor: str
    counterparty: str
    payload: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SubmissionReceipt:
    receipt_type: str
    work_order_id: str
    content_sha256: str
    status: WorkStatus
    duplicate: bool
    executor_run_id: str | None
    ledger_sequence: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
