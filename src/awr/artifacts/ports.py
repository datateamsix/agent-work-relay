from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import BinaryIO, Protocol

from .contracts import (
    Artifact,
    ArtifactPurpose,
    ArtifactReceipt,
    ArtifactSecurityReceipt,
    ArtifactStatus,
    ArtifactUploadTicket,
    ScanClaim,
)


class ArtifactSession(Protocol):
    def get_artifact(self) -> Artifact: ...

    def update_status(self, status: ArtifactStatus) -> None: ...

    def set_fingerprint(self, *, sha256: str, byte_length: int) -> None: ...

    def append_receipt(
        self,
        event_type: str,
        actor: str,
        counterparty: str,
        payload: dict[str, object],
    ) -> ArtifactReceipt: ...

    def list_receipts(self) -> list[ArtifactReceipt]: ...


class ArtifactMetadataStore(Protocol):
    def declare(
        self,
        artifact: Artifact,
    ) -> tuple[Artifact, bool]: ...

    def get(self, artifact_id: str) -> Artifact | None: ...

    def get_by_idempotency_key(self, owner: str, idempotency_key: str) -> Artifact | None: ...

    def update_status(self, artifact_id: str, status: ArtifactStatus) -> None: ...

    def put_security_receipt(self, receipt: ArtifactSecurityReceipt) -> ArtifactSecurityReceipt: ...

    def list_security_receipts(self, artifact_id: str) -> list[ArtifactSecurityReceipt]: ...

    def get_security_receipt_for_digest(
        self, artifact_id: str, sha256: str
    ) -> ArtifactSecurityReceipt | None: ...

    def list_artifacts(self) -> list[Artifact]: ...

    def claim_scan_lease(
        self,
        artifact_id: str,
        *,
        now: datetime,
        lease_ttl_seconds: float,
        lease_id: str,
    ) -> ScanClaim | None: ...

    def complete_scan(
        self,
        artifact_id: str,
        *,
        lease_id: str,
        status: ArtifactStatus,
        detected_media_type: str | None,
        now: datetime,
    ) -> Artifact: ...

    def append_receipt(
        self,
        artifact_id: str,
        event_type: str,
        actor: str,
        counterparty: str,
        payload: dict[str, object],
        *,
        correlation_id: str,
        work_order_id: str | None = None,
    ) -> ArtifactReceipt: ...

    def list_receipts(self, artifact_id: str) -> list[ArtifactReceipt]: ...

    def lock_artifact(self, artifact_id: str) -> AbstractContextManager[ArtifactSession]: ...

    def put_upload_ticket(self, ticket: ArtifactUploadTicket) -> ArtifactUploadTicket: ...

    def get_upload_ticket(self, artifact_id: str) -> ArtifactUploadTicket | None: ...

    def spend_upload_ticket(self, artifact_id: str, *, now: datetime) -> ArtifactUploadTicket: ...


class ArtifactBodyStore(Protocol):
    def write_quarantine(
        self,
        artifact_id: str,
        stream: BinaryIO,
        *,
        max_bytes: int,
    ) -> tuple[str, int]: ...

    def open_quarantine(
        self, artifact_id: str, sha256: str
    ) -> AbstractContextManager[BinaryIO]: ...

    def promote_clean(self, artifact_id: str, sha256: str) -> None: ...

    def open_clean(self, artifact_id: str, sha256: str) -> AbstractContextManager[BinaryIO]: ...

    def has_clean(self, artifact_id: str, sha256: str) -> bool: ...

    def has_quarantine(self, artifact_id: str, sha256: str) -> bool: ...

    def delete_generation(self, artifact_id: str, sha256: str) -> None: ...

    def delete_expired(self, now: datetime, max_age_seconds: float) -> int: ...


def safe_filename(original_filename: str) -> str:
    name = original_filename.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(char for char in name if char.isprintable() and char not in {"\x00"})
    return cleaned or "unnamed"


def require_purpose(value: str) -> ArtifactPurpose:
    try:
        return ArtifactPurpose(value)
    except ValueError as exc:
        raise ValueError(f"Unknown artifact purpose: {value!r}") from exc
