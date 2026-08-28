from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from ..artifacts.errors import ArtifactAccessError, ArtifactImmutabilityError, ArtifactTooLargeError

_CHUNK_SIZE = 64 * 1024
_SHA256_HEX_LENGTH = 64


def _require_digest(sha256: str) -> str:
    digest = sha256.strip().lower()
    if len(digest) != _SHA256_HEX_LENGTH or any(char not in "0123456789abcdef" for char in digest):
        raise ArtifactAccessError("Artifact digest must be a 64-character SHA-256 hex string.")
    return digest


def _require_artifact_id(artifact_id: str) -> str:
    if not artifact_id or "/" in artifact_id or "\\" in artifact_id or ".." in artifact_id:
        raise ArtifactAccessError("Artifact ID is not a valid storage key.")
    return artifact_id


class LocalArtifactBodyStore:
    """Filesystem body store with isolated quarantine and clean trees."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.tmp_root = self.root / "tmp"
        self.quarantine_root = self.root / "quarantine"
        self.clean_root = self.root / "clean"
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        self.quarantine_root.mkdir(parents=True, exist_ok=True)
        self.clean_root.mkdir(parents=True, exist_ok=True)

    def write_quarantine(
        self,
        artifact_id: str,
        stream: BinaryIO,
        *,
        max_bytes: int,
    ) -> tuple[str, int]:
        artifact_id = _require_artifact_id(artifact_id)
        if max_bytes <= 0:
            raise ArtifactTooLargeError("Artifact size limit must be positive.")
        temp_path = self.tmp_root / f"{artifact_id}.{uuid4().hex}.part"
        digest = hashlib.sha256()
        byte_length = 0
        try:
            with temp_path.open("wb") as handle:
                while True:
                    chunk = stream.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    byte_length += len(chunk)
                    if byte_length > max_bytes:
                        raise ArtifactTooLargeError(f"Artifact exceeds the {max_bytes} byte limit.")
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            sha256 = digest.hexdigest()
            destination = self._quarantine_path(artifact_id, sha256)
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._finalize_exclusive(temp_path, destination, sha256)
            self._fsync_directory(destination.parent)
            return sha256, byte_length
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def open_quarantine(self, artifact_id: str, sha256: str) -> AbstractContextManager[BinaryIO]:
        return self._open(self._quarantine_path(_require_artifact_id(artifact_id), sha256))

    def open_clean(self, artifact_id: str, sha256: str) -> AbstractContextManager[BinaryIO]:
        return self._open(self._clean_path(_require_artifact_id(artifact_id), sha256))

    def promote_clean(self, artifact_id: str, sha256: str) -> None:
        artifact_id = _require_artifact_id(artifact_id)
        digest = _require_digest(sha256)
        source = self._quarantine_path(artifact_id, digest)
        if not source.is_file():
            raise ArtifactAccessError("Quarantined object is not available for promotion.")
        destination = self._clean_path(artifact_id, digest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.tmp_root / f"{artifact_id}.{uuid4().hex}.promote"
        try:
            shutil.copyfile(source, temp_path)
            with temp_path.open("rb+") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            copied = self._sha256_file(temp_path)
            if copied != digest:
                raise ArtifactImmutabilityError(
                    "Promoted bytes do not match the quarantined digest."
                )
            self._finalize_exclusive(temp_path, destination, digest)
            self._fsync_directory(destination.parent)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def delete_expired(self, now: datetime, max_age_seconds: float) -> int:
        cutoff = now.astimezone(UTC).timestamp() - max_age_seconds
        removed = 0
        for directory in (self.quarantine_root, self.clean_root, self.tmp_root):
            for path in directory.rglob("*"):
                if not path.is_file():
                    continue
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
                    removed += 1
        return removed

    def _quarantine_path(self, artifact_id: str, sha256: str) -> Path:
        return self.quarantine_root / artifact_id / _require_digest(sha256)

    def _clean_path(self, artifact_id: str, sha256: str) -> Path:
        return self.clean_root / artifact_id / _require_digest(sha256)

    @staticmethod
    @contextmanager
    def _open(path: Path) -> Iterator[BinaryIO]:
        if not path.is_file():
            raise ArtifactAccessError(
                "Requested artifact body is not available in this store area."
            )
        handle = path.open("rb")
        try:
            yield handle
        finally:
            handle.close()

    def _finalize_exclusive(self, source: Path, destination: Path, sha256: str) -> None:
        if destination.exists():
            existing = self._sha256_file(destination)
            if existing != sha256:
                raise ArtifactImmutabilityError(
                    "An immutable artifact body already exists for this identifier."
                )
            source.unlink(missing_ok=True)
            return
        try:
            os.replace(source, destination)
        except FileExistsError as exc:
            existing = self._sha256_file(destination)
            source.unlink(missing_ok=True)
            if existing != sha256:
                raise ArtifactImmutabilityError(
                    "An immutable artifact body already exists for this identifier."
                ) from exc

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        directory = os.open(path, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
