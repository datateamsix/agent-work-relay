from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import BinaryIO
from uuid import uuid4

from .contracts import Artifact, ArtifactPurpose, ArtifactStatus
from .errors import ArtifactError, ArtifactTooLargeError
from .ports import ArtifactBodyStore, ArtifactMetadataStore, require_purpose, safe_filename

DEFAULT_MAX_BYTES = 10 * 1024 * 1024


class ArtifactService:
    """Declare and quarantine supporting artifacts without executor delivery."""

    def __init__(
        self,
        metadata: ArtifactMetadataStore,
        bodies: ArtifactBodyStore,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self.metadata = metadata
        self.bodies = bodies
        self.max_bytes = max_bytes

    def declare(
        self,
        *,
        owner: str,
        original_filename: str,
        declared_media_type: str,
        purpose: ArtifactPurpose | str,
        idempotency_key: str,
        expected_byte_length: int | None = None,
        expected_sha256: str | None = None,
        parent_artifact_id: str | None = None,
    ) -> tuple[Artifact, bool]:
        resolved_purpose = (
            purpose if isinstance(purpose, ArtifactPurpose) else require_purpose(purpose)
        )
        if expected_byte_length is not None and expected_byte_length > self.max_bytes:
            raise ArtifactTooLargeError(f"Artifact exceeds the {self.max_bytes} byte limit.")
        candidate = Artifact(
            artifact_id=f"ART-{uuid4()}",
            idempotency_key=idempotency_key,
            owner=owner,
            original_filename=original_filename,
            declared_media_type=declared_media_type,
            detected_media_type=None,
            byte_length=None,
            sha256=None,
            purpose=resolved_purpose,
            status=ArtifactStatus.DECLARED,
            created_at=datetime.now(UTC).isoformat(),
            parent_artifact_id=parent_artifact_id,
            correlation_id=f"corr-{uuid4()}",
            expected_byte_length=expected_byte_length,
            expected_sha256=expected_sha256.lower() if expected_sha256 else None,
        )
        return self.metadata.declare(candidate)

    def finalize_stream(
        self,
        artifact_id: str,
        stream: BinaryIO,
        *,
        actor: str | None = None,
    ) -> Artifact:
        failure: Exception | None = None
        with self.metadata.lock_artifact(artifact_id) as session:
            artifact = session.get_artifact()
            actor_id = actor or artifact.owner
            if artifact.status is ArtifactStatus.QUARANTINED and artifact.sha256 is not None:
                digest, byte_length = self._hash_stream(stream)
                if digest != artifact.sha256 or byte_length != artifact.byte_length:
                    raise ArtifactError("Finalized bytes do not match the quarantined artifact.")
                return session.get_artifact()
            if artifact.status is not ArtifactStatus.DECLARED:
                raise ArtifactError(
                    f"Artifact {artifact_id} cannot accept bytes in status {artifact.status.value}."
                )
            try:
                sha256, byte_length = self.bodies.write_quarantine(
                    artifact.artifact_id,
                    stream,
                    max_bytes=self.max_bytes,
                )
            except ArtifactTooLargeError as exc:
                session.update_status(ArtifactStatus.REJECTED_SIZE)
                session.append_receipt(
                    "artifact.rejected",
                    actor_id,
                    "broker:awr",
                    {
                        "reason": ArtifactStatus.REJECTED_SIZE.value,
                        "limit_bytes": self.max_bytes,
                    },
                )
                failure = exc
            else:
                if artifact.expected_sha256 and sha256 != artifact.expected_sha256:
                    session.update_status(ArtifactStatus.REJECTED_TAMPERING)
                    session.append_receipt(
                        "artifact.rejected",
                        actor_id,
                        "broker:awr",
                        {
                            "reason": ArtifactStatus.REJECTED_TAMPERING.value,
                            "sha256": sha256,
                            "byte_length": byte_length,
                        },
                    )
                    failure = ArtifactError("Streamed digest does not match the declared SHA-256.")
                elif (
                    artifact.expected_byte_length is not None
                    and byte_length != artifact.expected_byte_length
                ):
                    session.update_status(ArtifactStatus.REJECTED_TAMPERING)
                    session.append_receipt(
                        "artifact.rejected",
                        actor_id,
                        "broker:awr",
                        {
                            "reason": ArtifactStatus.REJECTED_TAMPERING.value,
                            "sha256": sha256,
                            "byte_length": byte_length,
                        },
                    )
                    failure = ArtifactError(
                        "Streamed length does not match the declared byte length."
                    )
                else:
                    session.set_fingerprint(sha256=sha256, byte_length=byte_length)
                    session.update_status(ArtifactStatus.QUARANTINED)
                    session.append_receipt(
                        "artifact.quarantined",
                        actor_id,
                        "broker:awr",
                        {
                            "sha256": sha256,
                            "byte_length": byte_length,
                            "declared_media_type": artifact.declared_media_type,
                            "purpose": artifact.purpose.value,
                            "safe_filename": safe_filename(artifact.original_filename),
                        },
                    )
                    return session.get_artifact()
        if failure is not None:
            raise failure
        raise ArtifactError(f"Artifact {artifact_id} was not finalized.")

    @staticmethod
    def _hash_stream(stream: BinaryIO) -> tuple[str, int]:
        digest = hashlib.sha256()
        byte_length = 0
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            byte_length += len(chunk)
            digest.update(chunk)
        return digest.hexdigest(), byte_length
