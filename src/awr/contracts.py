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
    WAITING_FOR_PLAN_APPROVAL = "WAITING_FOR_PLAN_APPROVAL"
    READY_FOR_EXECUTION = "READY_FOR_EXECUTION"
    EXECUTION_DISPATCHED = "EXECUTION_DISPATCHED"
    EXECUTING = "EXECUTING"
    COMPLETION_READY = "COMPLETION_READY"
    PLANNER_REVIEWING = "PLANNER_REVIEWING"
    REVISION_REQUIRED = "REVISION_REQUIRED"
    WAITING_FOR_HUMAN_REVIEW = "WAITING_FOR_HUMAN_REVIEW"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in {WorkStatus.COMPLETE, WorkStatus.FAILED, WorkStatus.CANCELLED}


class ExecutorRunStatus(StrEnum):
    CREATING = "CREATING"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"

    @property
    def terminal(self) -> bool:
        return self in {
            ExecutorRunStatus.FINISHED,
            ExecutorRunStatus.ERROR,
            ExecutorRunStatus.CANCELLED,
            ExecutorRunStatus.EXPIRED,
        }


@dataclass(frozen=True, slots=True)
class Directive:
    kind: WorkKind
    action: WorkAction
    parent_work_order_id: str | None = None
    form: str = "awr_alias"

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
    repository_url: str
    base_ref: str
    markdown: str
    content_sha256: str
    wrapper_id: str
    wrapper_version: str
    wrapper_sha256: str
    status: WorkStatus
    created_at: str
    bundle_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PlanningDispatch:
    work_order_id: str
    recipient: str
    mode: str
    repository_url: str
    base_ref: str
    existing_agent_id: str | None
    wrapped_markdown: str
    content_sha256: str
    wrapper_id: str
    wrapper_version: str
    wrapper_sha256: str


@dataclass(frozen=True, slots=True)
class ExecutorAcknowledgement:
    executor_agent_id: str
    executor_run_id: str
    executor: str
    executor_url: str | None
    accepted: bool
    message: str


@dataclass(frozen=True, slots=True)
class PlanningRunResult:
    executor_agent_id: str
    executor_run_id: str
    executor: str
    status: ExecutorRunStatus
    result: str | None = None
    duration_ms: int | None = None
    git: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PlanningStatusReceipt:
    work_order_id: str
    status: WorkStatus
    executor_status: ExecutorRunStatus
    executor_agent_id: str
    executor_run_id: str
    ledger_sequence: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PlanPacket:
    plan_id: str
    work_order_id: str
    executor: str
    executor_agent_id: str
    executor_run_id: str
    content: str
    content_sha256: str
    duration_ms: int | None
    git: dict[str, Any] | None
    completed_at: str
    ledger_sequence: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    bundle_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
