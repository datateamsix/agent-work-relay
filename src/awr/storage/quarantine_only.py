from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import BinaryIO

from ..artifacts.errors import ArtifactAccessError
from .artifact_fs import LocalArtifactBodyStore


class QuarantineOnlyBodyStore:
    """Intake facade: quarantine writes and reads only. Promotion is orchestrator-only."""

    def __init__(self, inner: LocalArtifactBodyStore) -> None:
        self._inner = inner

    def write_quarantine(
        self,
        artifact_id: str,
        stream: BinaryIO,
        *,
        max_bytes: int,
    ) -> tuple[str, int]:
        return self._inner.write_quarantine(artifact_id, stream, max_bytes=max_bytes)

    def open_quarantine(self, artifact_id: str, sha256: str) -> AbstractContextManager[BinaryIO]:
        return self._inner.open_quarantine(artifact_id, sha256)

    def promote_clean(self, artifact_id: str, sha256: str) -> None:
        del artifact_id, sha256
        raise ArtifactAccessError("promote_clean is not available on the artifact intake path.")

    def open_clean(self, artifact_id: str, sha256: str) -> AbstractContextManager[BinaryIO]:
        del artifact_id, sha256
        raise ArtifactAccessError("Clean artifact bodies are not available on the intake path.")

    def has_clean(self, artifact_id: str, sha256: str) -> bool:
        del artifact_id, sha256
        return False

    def has_quarantine(self, artifact_id: str, sha256: str) -> bool:
        return self._inner.has_quarantine(artifact_id, sha256)

    def delete_generation(self, artifact_id: str, sha256: str) -> None:
        self._inner.delete_generation(artifact_id, sha256)

    def delete_expired(self, now: datetime, max_age_seconds: float) -> int:
        return self._inner.delete_expired(now, max_age_seconds)
