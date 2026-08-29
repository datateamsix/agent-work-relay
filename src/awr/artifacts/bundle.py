from __future__ import annotations

import hashlib

from .contracts import (
    Artifact,
    ArtifactReference,
    ArtifactSecurityVerdict,
    ArtifactStatus,
    WorkBundle,
    status_from_security_receipt,
)
from .ports import ArtifactBodyStore, ArtifactMetadataStore, safe_filename

BUNDLE_MARKDOWN_MAX_BYTES = 256 * 1024
BUNDLE_MAX_ARTIFACTS = 10
BUNDLE_MAX_BYTES = 25 * 1024 * 1024
_CHUNK = 64 * 1024


class BundleValidationError(ValueError):
    """A work bundle cannot be accepted without violating a broker invariant."""


def fingerprint_bundle(markdown: str, references: tuple[ArtifactReference, ...]) -> str:
    ordered = _ordered(references)
    markdown_sha256 = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    parts = [markdown_sha256, str(len(ordered))]
    parts.extend(f"{item.artifact_id}:{item.sha256}:{item.purpose.value}" for item in ordered)
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def resolve_bundle(
    markdown: str,
    artifact_ids: tuple[str, ...],
    *,
    sender: str,
    metadata: ArtifactMetadataStore,
    bodies: ArtifactBodyStore,
    markdown_max_bytes: int = BUNDLE_MARKDOWN_MAX_BYTES,
    max_artifacts: int = BUNDLE_MAX_ARTIFACTS,
    max_bundle_bytes: int = BUNDLE_MAX_BYTES,
) -> WorkBundle:
    markdown_bytes = len(markdown.encode("utf-8"))
    if markdown_bytes > markdown_max_bytes:
        raise BundleValidationError(f"Bundle Markdown exceeds the {markdown_max_bytes} byte limit.")
    if len(artifact_ids) != len(set(artifact_ids)):
        raise BundleValidationError("Bundle artifact IDs must be unique.")
    if len(artifact_ids) > max_artifacts:
        raise BundleValidationError(f"Bundle has too many artifacts (max {max_artifacts}).")
    references = tuple(
        _attachable_reference(artifact_id, sender=sender, metadata=metadata, bodies=bodies)
        for artifact_id in artifact_ids
    )
    total = markdown_bytes + sum(item.byte_length for item in references)
    if total > max_bundle_bytes:
        raise BundleValidationError(f"Bundle exceeds the {max_bundle_bytes} byte limit.")
    ordered = _ordered(references)
    return WorkBundle(
        markdown=markdown,
        references=ordered,
        bundle_sha256=fingerprint_bundle(markdown, ordered),
    )


def verify_bundle_generation(
    bundle: WorkBundle,
    *,
    sender: str,
    metadata: ArtifactMetadataStore,
    bodies: ArtifactBodyStore,
) -> None:
    for reference in bundle.references:
        _attachable_reference(
            reference.artifact_id,
            sender=sender,
            metadata=metadata,
            bodies=bodies,
            expected=reference,
        )


def reference_payloads(bundle: WorkBundle) -> list[dict[str, object]]:
    return [reference.to_dict() for reference in bundle.references]


def _attachable_reference(
    artifact_id: str,
    *,
    sender: str,
    metadata: ArtifactMetadataStore,
    bodies: ArtifactBodyStore,
    expected: ArtifactReference | None = None,
) -> ArtifactReference:
    artifact = metadata.get(artifact_id)
    if artifact is None:
        raise BundleValidationError(f"Bundle references a missing artifact: {artifact_id}.")
    if artifact.owner != sender:
        raise BundleValidationError("Bundle references an artifact owned by another actor.")
    if artifact.status is ArtifactStatus.CLEAN:
        return _clean_reference(artifact, metadata=metadata, bodies=bodies, expected=expected)
    if artifact.status.terminal or artifact.status.value.startswith("REJECTED_"):
        raise BundleValidationError("Bundle references a rejected artifact.")
    raise BundleValidationError("Bundle references a pending artifact.")


def _clean_reference(
    artifact: Artifact,
    *,
    metadata: ArtifactMetadataStore,
    bodies: ArtifactBodyStore,
    expected: ArtifactReference | None,
) -> ArtifactReference:
    if not artifact.sha256 or artifact.byte_length is None:
        raise BundleValidationError("Bundle references a tampered artifact.")
    receipt = metadata.get_security_receipt_for_digest(artifact.artifact_id, artifact.sha256)
    if receipt is None:
        raise BundleValidationError("Bundle references a pending artifact.")
    if receipt.verdict is not ArtifactSecurityVerdict.CLEAN:
        raise BundleValidationError("Bundle references a rejected artifact.")
    if status_from_security_receipt(receipt) is not ArtifactStatus.CLEAN:
        raise BundleValidationError("Bundle references a rejected artifact.")
    if receipt.scanned_sha256 != artifact.sha256:
        raise BundleValidationError("Bundle references a tampered artifact.")
    if not bodies.has_clean(artifact.artifact_id, artifact.sha256):
        raise BundleValidationError("Bundle references an expired artifact.")
    digest, byte_length = _hash_clean(bodies, artifact.artifact_id, artifact.sha256)
    if digest != artifact.sha256 or byte_length != artifact.byte_length:
        raise BundleValidationError("Bundle references a tampered artifact.")
    reference = ArtifactReference(
        artifact_id=artifact.artifact_id,
        purpose=artifact.purpose,
        byte_length=artifact.byte_length,
        sha256=artifact.sha256,
        detected_media_type=artifact.detected_media_type,
        safe_filename=safe_filename(artifact.original_filename),
    )
    if expected is not None and (
        expected.sha256 != reference.sha256 or expected.byte_length != reference.byte_length
    ):
        raise BundleValidationError("Bundle references a tampered artifact.")
    return reference


def _hash_clean(bodies: ArtifactBodyStore, artifact_id: str, sha256: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    byte_length = 0
    with bodies.open_clean(artifact_id, sha256) as handle:
        while True:
            chunk: bytes = handle.read(_CHUNK)
            if not chunk:
                break
            byte_length += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), byte_length


def _ordered(references: tuple[ArtifactReference, ...]) -> tuple[ArtifactReference, ...]:
    return tuple(sorted(references, key=lambda item: (item.purpose.value, item.artifact_id)))
