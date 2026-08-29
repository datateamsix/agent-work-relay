from __future__ import annotations

from typing import Any, BinaryIO

from .clock import Clock, UtcClock
from .contracts import Artifact, ArtifactPurpose, ArtifactSecurityReceipt, ArtifactStatus
from .errors import ArtifactError
from .ports import ArtifactBodyStore, ArtifactMetadataStore, require_purpose, safe_filename
from .security import ArtifactSecurityService
from .service import ArtifactService
from .tickets import TicketService


class ArtifactRelay:
    """Planner-facing intake, ticket, and inspect orchestration."""

    def __init__(
        self,
        intake: ArtifactService,
        security: ArtifactSecurityService,
        metadata: ArtifactMetadataStore,
        bodies: ArtifactBodyStore,
        *,
        clock: Clock | None = None,
        ticket_ttl_seconds: float = 900.0,
        public_base_url: str = "",
    ) -> None:
        self.intake = intake
        self.security = security
        self.metadata = metadata
        self.bodies = bodies
        self.clock = clock or UtcClock()
        self.tickets = TicketService(
            metadata,
            clock=self.clock,
            ttl_seconds=ticket_ttl_seconds,
            max_bytes=intake.max_bytes,
        )
        self.public_base_url = public_base_url.rstrip("/")

    def begin_intake(
        self,
        *,
        owner: str,
        original_filename: str,
        declared_media_type: str,
        purpose: ArtifactPurpose | str,
        idempotency_key: str,
        expected_byte_length: int | None = None,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        resolved = purpose if isinstance(purpose, ArtifactPurpose) else require_purpose(purpose)
        artifact, created = self.intake.declare(
            owner=owner,
            original_filename=original_filename,
            declared_media_type=declared_media_type,
            purpose=resolved,
            idempotency_key=idempotency_key,
            expected_byte_length=expected_byte_length,
            expected_sha256=expected_sha256,
        )
        token: str | None = None
        expires_at: str | None = None
        if artifact.status is ArtifactStatus.DECLARED:
            ticket, token = self.tickets.issue(artifact.artifact_id, owner)
            expires_at = ticket.expires_at
        return {
            "artifact_id": artifact.artifact_id,
            "status": artifact.status.value,
            "upload_url": self._upload_url(artifact.artifact_id),
            "upload_token": token,
            "expires_at": expires_at,
            "max_bytes": self.intake.max_bytes,
            "duplicate": not created,
        }

    def upload_content(
        self,
        artifact_id: str,
        stream: BinaryIO,
        *,
        actor: str,
        token: str,
    ) -> Artifact:
        self.tickets.require_open(artifact_id, actor=actor, token=token)
        artifact = self.intake.finalize_stream(artifact_id, stream, actor=actor)
        self.tickets.spend(artifact_id)
        return artifact

    def finalize_upload(self, artifact_id: str, *, actor: str) -> dict[str, Any]:
        artifact = self._owned(artifact_id, actor)
        if artifact.status is ArtifactStatus.DECLARED:
            raise ArtifactError("Artifact has not been uploaded.")
        if artifact.status in {ArtifactStatus.QUARANTINED, ArtifactStatus.SCANNING}:
            inspected = self.security.inspect(artifact_id)
            artifact = self.metadata.get(artifact_id) or artifact
            return self._status_view(artifact, inspected)
        existing: ArtifactSecurityReceipt | None = None
        if artifact.sha256:
            existing = self.metadata.get_security_receipt_for_digest(artifact_id, artifact.sha256)
        return self._status_view(artifact, existing)

    def get_status(self, artifact_id: str, *, actor: str) -> dict[str, Any]:
        artifact = self._owned(artifact_id, actor)
        receipt = None
        if artifact.sha256:
            receipt = self.metadata.get_security_receipt_for_digest(artifact_id, artifact.sha256)
        return self._status_view(artifact, receipt)

    def _owned(self, artifact_id: str, actor: str) -> Artifact:
        artifact = self.metadata.get(artifact_id)
        if artifact is None or artifact.owner != actor:
            raise KeyError(f"Unknown artifact: {artifact_id}")
        return artifact

    def _upload_url(self, artifact_id: str) -> str:
        path = f"/v1/artifacts/{artifact_id}/content"
        if not self.public_base_url:
            return path
        return f"{self.public_base_url}{path}"

    @staticmethod
    def _status_view(artifact: Artifact, receipt: ArtifactSecurityReceipt | None) -> dict[str, Any]:
        return {
            "artifact_id": artifact.artifact_id,
            "status": artifact.status.value,
            "owner": artifact.owner,
            "purpose": artifact.purpose.value,
            "sha256": artifact.sha256,
            "byte_length": artifact.byte_length,
            "detected_media_type": artifact.detected_media_type,
            "safe_filename": safe_filename(artifact.original_filename),
            "security_receipt": None if receipt is None else receipt.to_dict(),
        }
