from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Never


class ArtifactStatus(StrEnum):
    DECLARED = "DECLARED"
    QUARANTINED = "QUARANTINED"
    SCANNING = "SCANNING"
    CLEAN = "CLEAN"
    READY_TO_RELAY = "READY_TO_RELAY"
    RELAYED = "RELAYED"
    REJECTED_SIZE = "REJECTED_SIZE"
    REJECTED_TYPE = "REJECTED_TYPE"
    REJECTED_MALWARE = "REJECTED_MALWARE"
    REJECTED_ACTIVE_CONTENT = "REJECTED_ACTIVE_CONTENT"
    REJECTED_MALFORMED = "REJECTED_MALFORMED"
    REJECTED_TAMPERING = "REJECTED_TAMPERING"
    REJECTED_SCANNER_UNAVAILABLE = "REJECTED_SCANNER_UNAVAILABLE"

    @property
    def terminal(self) -> bool:
        return self in _TERMINAL_STATUSES


class ArtifactPurpose(StrEnum):
    DESIGN_REFERENCE = "design_reference"
    DATA_CONTRACT = "data_contract"
    REQUIREMENTS_REFERENCE = "requirements_reference"
    OTHER_REFERENCE = "other_reference"


class ArtifactSecurityVerdict(StrEnum):
    CLEAN = "CLEAN"
    MALICIOUS = "MALICIOUS"
    INCONCLUSIVE = "INCONCLUSIVE"
    UNAVAILABLE = "UNAVAILABLE"


_REJECTION_STATUSES = frozenset(
    {
        ArtifactStatus.REJECTED_SIZE,
        ArtifactStatus.REJECTED_TYPE,
        ArtifactStatus.REJECTED_MALWARE,
        ArtifactStatus.REJECTED_ACTIVE_CONTENT,
        ArtifactStatus.REJECTED_MALFORMED,
        ArtifactStatus.REJECTED_TAMPERING,
        ArtifactStatus.REJECTED_SCANNER_UNAVAILABLE,
    }
)
_TERMINAL_STATUSES = _REJECTION_STATUSES | {ArtifactStatus.RELAYED}


def _assert_never(value: Never) -> Never:
    raise ValueError(f"Unhandled artifact status: {value!r}")


def allowed_transitions(status: ArtifactStatus) -> frozenset[ArtifactStatus]:
    match status:
        case ArtifactStatus.DECLARED:
            return frozenset({ArtifactStatus.QUARANTINED, *_REJECTION_STATUSES})
        case ArtifactStatus.QUARANTINED:
            return frozenset({ArtifactStatus.SCANNING, *_REJECTION_STATUSES})
        case ArtifactStatus.SCANNING:
            return frozenset({ArtifactStatus.CLEAN, *_REJECTION_STATUSES})
        case ArtifactStatus.CLEAN:
            return frozenset({ArtifactStatus.READY_TO_RELAY})
        case ArtifactStatus.READY_TO_RELAY:
            return frozenset({ArtifactStatus.RELAYED})
        case ArtifactStatus.RELAYED:
            return frozenset()
        case (
            ArtifactStatus.REJECTED_SIZE
            | ArtifactStatus.REJECTED_TYPE
            | ArtifactStatus.REJECTED_MALWARE
            | ArtifactStatus.REJECTED_ACTIVE_CONTENT
            | ArtifactStatus.REJECTED_MALFORMED
            | ArtifactStatus.REJECTED_TAMPERING
            | ArtifactStatus.REJECTED_SCANNER_UNAVAILABLE
        ):
            return frozenset()
        case _:
            return _assert_never(status)


@dataclass(frozen=True, slots=True)
class Artifact:
    artifact_id: str
    idempotency_key: str
    owner: str
    original_filename: str
    declared_media_type: str
    detected_media_type: str | None
    byte_length: int | None
    sha256: str | None
    purpose: ArtifactPurpose
    status: ArtifactStatus
    created_at: str
    parent_artifact_id: str | None
    correlation_id: str
    expected_byte_length: int | None = None
    expected_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["purpose"] = self.purpose.value
        payload["status"] = self.status.value
        return payload


@dataclass(frozen=True, slots=True)
class ArtifactSecurityReceipt:
    receipt_id: str
    artifact_id: str
    scanner_id: str
    scanner_version: str
    signature_version: str
    verdict: ArtifactSecurityVerdict
    reason_codes: tuple[str, ...]
    scanned_sha256: str
    started_at: str
    completed_at: str
    diagnostics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["verdict"] = self.verdict.value
        payload["reason_codes"] = list(self.reason_codes)
        return payload


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    artifact_id: str
    purpose: ArtifactPurpose
    byte_length: int
    sha256: str
    detected_media_type: str | None
    safe_filename: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "purpose": self.purpose.value,
            "byte_length": self.byte_length,
            "sha256": self.sha256,
            "detected_media_type": self.detected_media_type,
            "safe_filename": self.safe_filename,
        }


@dataclass(frozen=True, slots=True)
class ArtifactReceipt:
    sequence: int
    event_id: str
    artifact_id: str
    work_order_id: str | None
    correlation_id: str
    event_type: str
    actor: str
    counterparty: str
    payload: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
