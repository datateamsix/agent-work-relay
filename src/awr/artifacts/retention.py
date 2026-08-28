from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from ..artifacts.clock import Clock, UtcClock
from ..artifacts.contracts import (
    REASON_EXPIRED_UNCLAIMED,
    REASON_SCANNER_UNAVAILABLE,
    Artifact,
    ArtifactSecurityReceipt,
    ArtifactSecurityVerdict,
    ArtifactStatus,
    is_rejection,
)
from ..artifacts.ports import ArtifactBodyStore, ArtifactMetadataStore

_ACTOR = "broker:awr"


class ArtifactRetention:
    """Deletes expired bodies while preserving receipts and metadata."""

    def __init__(
        self,
        metadata: ArtifactMetadataStore,
        bodies: ArtifactBodyStore,
        *,
        clock: Clock | None = None,
        declare_ttl_seconds: float = 86400.0,
        clean_ttl_seconds: float = 604800.0,
    ) -> None:
        self.metadata = metadata
        self.bodies = bodies
        self.clock = clock or UtcClock()
        self.declare_ttl_seconds = declare_ttl_seconds
        self.clean_ttl_seconds = clean_ttl_seconds

    def purge(self, now: datetime | None = None) -> int:
        moment = now or self.clock.now()
        removed = 0
        for artifact in self.metadata.list_artifacts():
            removed += self._purge_one(artifact, moment)
        return removed

    def _purge_one(self, artifact: Artifact, now: datetime) -> int:
        age = _age_seconds(artifact.created_at, now)
        if artifact.status is ArtifactStatus.SCANNING:
            if self._expired_scan_without_receipt(artifact, now):
                self._reject_unavailable(artifact, now, REASON_SCANNER_UNAVAILABLE)
                artifact = self.metadata.get(artifact.artifact_id) or artifact
            else:
                return 0
        if artifact.status in {ArtifactStatus.DECLARED, ArtifactStatus.QUARANTINED}:
            if age < self.declare_ttl_seconds:
                return 0
            self._reject_unavailable(artifact, now, REASON_EXPIRED_UNCLAIMED)
            artifact = self.metadata.get(artifact.artifact_id) or artifact
        if is_rejection(artifact.status):
            if age < self.declare_ttl_seconds:
                return 0
            return self._delete_bodies(artifact)
        if artifact.status is ArtifactStatus.CLEAN:
            if age < self.clean_ttl_seconds:
                return 0
            return self._delete_bodies(artifact)
        return 0

    def _expired_scan_without_receipt(self, artifact: Artifact, now: datetime) -> bool:
        if artifact.sha256:
            receipt = self.metadata.get_security_receipt_for_digest(
                artifact.artifact_id, artifact.sha256
            )
            if receipt is not None:
                return False
        expires = artifact.scan_lease_expires_at
        if not expires:
            return True
        return _parse_iso(expires) <= now

    def _reject_unavailable(self, artifact: Artifact, now: datetime, reason: str) -> None:
        if artifact.status is ArtifactStatus.CLEAN or is_rejection(artifact.status):
            return
        if artifact.sha256:
            existing = self.metadata.get_security_receipt_for_digest(
                artifact.artifact_id, artifact.sha256
            )
            if existing is None:
                stamp = now.isoformat()
                self.metadata.put_security_receipt(
                    ArtifactSecurityReceipt(
                        receipt_id=f"scr-{uuid4()}",
                        artifact_id=artifact.artifact_id,
                        scanner_id="retention",
                        scanner_version="1",
                        signature_version="0",
                        verdict=ArtifactSecurityVerdict.UNAVAILABLE,
                        reason_codes=(reason, REASON_SCANNER_UNAVAILABLE),
                        scanned_sha256=artifact.sha256,
                        started_at=stamp,
                        completed_at=stamp,
                        diagnostics={
                            "artifact_status": ArtifactStatus.REJECTED_SCANNER_UNAVAILABLE.value,
                            "control_authority": "primary_markdown_only",
                        },
                    )
                )
        if artifact.status is ArtifactStatus.SCANNING:
            self.metadata.complete_scan(
                artifact.artifact_id,
                lease_id=artifact.scan_lease_id or "",
                status=ArtifactStatus.REJECTED_SCANNER_UNAVAILABLE,
                detected_media_type=artifact.detected_media_type,
                now=now,
            )
            return
        if artifact.status in {ArtifactStatus.DECLARED, ArtifactStatus.QUARANTINED}:
            with self.metadata.lock_artifact(artifact.artifact_id) as session:
                current = session.get_artifact()
                if current.status in {ArtifactStatus.DECLARED, ArtifactStatus.QUARANTINED}:
                    session.update_status(ArtifactStatus.REJECTED_SCANNER_UNAVAILABLE)
                    session.append_receipt(
                        "artifact.rejected",
                        _ACTOR,
                        _ACTOR,
                        {"reason": ArtifactStatus.REJECTED_SCANNER_UNAVAILABLE.value},
                    )

    def _delete_bodies(self, artifact: Artifact) -> int:
        if not artifact.sha256:
            return 0
        had_clean = self.bodies.has_clean(artifact.artifact_id, artifact.sha256)
        had_quarantine = self.bodies.has_quarantine(artifact.artifact_id, artifact.sha256)
        self.bodies.delete_generation(artifact.artifact_id, artifact.sha256)
        return int(had_clean) + int(had_quarantine)


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _age_seconds(created_at: str, now: datetime) -> float:
    created = _parse_iso(created_at)
    return (now - created).total_seconds()
