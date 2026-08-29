from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from ..contracts import ExecutorRunStatus


class DispatchState(StrEnum):
    PENDING = "PENDING"
    LEASED = "LEASED"
    PROVIDER_ACCEPTED = "PROVIDER_ACCEPTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    FAILED = "FAILED"


class AmbiguousAcceptance(RuntimeError):
    """Provider acceptance cannot be proven without risking a duplicate run."""

    def __init__(self, message: str = "Provider acceptance is ambiguous.") -> None:
        super().__init__(message)
        self.code = "RECONCILIATION_REQUIRED"


class DeliveryUnsupported(ValueError):
    """Execution requires an artifact that cannot be delivered in this slice."""

    def __init__(self, message: str = "Artifact delivery is not supported for execution.") -> None:
        super().__init__(message)
        self.code = "DELIVERY_UNSUPPORTED"


class MalformedExecutorResponse(ValueError):
    """Terminal provider output is not a valid awr.response/v1 packet."""

    def __init__(self, message: str = "Executor returned a malformed response.") -> None:
        super().__init__(message)
        self.code = "MALFORMED_EXECUTOR_RESPONSE"


@dataclass(frozen=True, slots=True)
class ExecutionCapabilities:
    follow_up_reuse: bool
    client_supplied_agent_id: bool
    run_idempotency_header: bool
    list_runs: bool


@dataclass(frozen=True, slots=True)
class ExecutionDispatch:
    dispatch_id: str
    work_order_id: str
    attempt: int
    plan_id: str
    plan_sha256: str
    approval_decision_id: str
    executor: str
    repository_url: str
    base_ref: str
    wrapper_id: str
    wrapper_version: str
    wrapper_sha256: str
    provider_idempotency_key: str
    wrapped_markdown: str
    existing_agent_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionAcknowledgement:
    executor_agent_id: str
    executor_run_id: str
    executor: str
    accepted: bool
    message: str
    executor_url: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionRunResult:
    executor_agent_id: str
    executor_run_id: str
    executor: str
    status: ExecutorRunStatus
    result: str | None = None
    duration_ms: int | None = None
    git: dict[str, Any] | None = None


class ExecutionExecutor(Protocol):
    name: str

    def submit_for_execution(self, dispatch: ExecutionDispatch) -> ExecutionAcknowledgement: ...

    def recover_execution_submission(
        self, dispatch: ExecutionDispatch
    ) -> ExecutionAcknowledgement | None: ...

    def get_execution_run(
        self, executor_agent_id: str, executor_run_id: str
    ) -> ExecutionRunResult: ...

    def capabilities(self) -> ExecutionCapabilities: ...
