from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Never

from ..artifacts.contracts import ArtifactReference

RESPONSE_SCHEMA = "awr.response/v1"
RESPONSE_AUTHORITY = "report_only"
POLICY_VERSION = "control_authority.primary_markdown_only@1"
RENDER_TEMPLATE_ID = "awr.response.markdown"
RENDER_TEMPLATE_VERSION = "1.0.0"

MAX_STRING_BYTES = 8 * 1024
MAX_BODY_BYTES = 256 * 1024
MAX_COLLECTION = 32
MAX_EVIDENCE_REFS = 10
MAX_PACKET_BYTES = 512 * 1024

FORBIDDEN_INLINE_KEYS = frozenset(
    {
        "bytes",
        "diff",
        "log",
        "logs",
        "path",
        "report",
        "screenshot",
        "url",
        "signed_url",
    }
)


class ResponseType(StrEnum):
    RECEIPT_ACCEPTED = "receipt.accepted"
    PLAN_COMPLETED = "plan.completed"
    QUESTION_BLOCKED = "question.blocked"
    EXECUTION_ACKNOWLEDGED = "execution.acknowledged"
    EXECUTION_PROGRESS = "execution.progress"
    EXECUTION_COMPLETED = "execution.completed"
    EXECUTION_FAILED = "execution.failed"
    REVIEW_COMPLETED = "review.completed"


class ReviewOutcome(StrEnum):
    APPROVED = "APPROVED"
    REVISE = "REVISE"
    REJECTED = "REJECTED"


def assert_never_response_type(value: Never) -> Never:
    raise ValueError(f"Unhandled response type: {value!r}")


@dataclass(frozen=True, slots=True)
class ResponsePacket:
    schema: str
    response_type: ResponseType
    work_order_id: str
    in_reply_to: str
    idempotency_key: str
    source_input_sha256: str
    created_at: str
    authority: str
    payload: dict[str, Any]
    evidence_refs: tuple[ArtifactReference, ...] = ()
    message_id: str | None = None
    correlation_id: str | None = None
    executor_run_id: str | None = None
    actor: str | None = None
    content_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "response_type": self.response_type.value,
            "work_order_id": self.work_order_id,
            "in_reply_to": self.in_reply_to,
            "idempotency_key": self.idempotency_key,
            "source_input_sha256": self.source_input_sha256,
            "created_at": self.created_at,
            "authority": self.authority,
            "payload": self.payload,
            "evidence_refs": [item.to_dict() for item in self.evidence_refs],
            "message_id": self.message_id,
            "correlation_id": self.correlation_id,
            "executor_run_id": self.executor_run_id,
            "actor": self.actor,
            "content_sha256": self.content_sha256,
        }
