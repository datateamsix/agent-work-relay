from __future__ import annotations

import hashlib
import io
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from awr.artifacts.errors import (
    ArtifactAccessError,
    ArtifactImmutabilityError,
    ArtifactTooLargeError,
)
from awr.artifacts.ports import safe_filename
from awr.storage.artifact_fs import LocalArtifactBodyStore


class LocalArtifactBodyStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = LocalArtifactBodyStore(Path(self.temp_dir.name) / "artifacts")
        self.payload = b"{" + b'"ok": true' + b"}"
        self.digest = hashlib.sha256(self.payload).hexdigest()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_stream_records_sha256_and_length(self) -> None:
        sha256, byte_length = self.store.write_quarantine(
            "ART-1", io.BytesIO(self.payload), max_bytes=10 * 1024 * 1024
        )
        self.assertEqual(sha256, self.digest)
        self.assertEqual(byte_length, len(self.payload))
        with self.store.open_quarantine("ART-1", sha256) as handle:
            self.assertEqual(handle.read(), self.payload)

    def test_oversize_leaves_no_quarantine_or_clean_object(self) -> None:
        with self.assertRaises(ArtifactTooLargeError):
            self.store.write_quarantine("ART-big", io.BytesIO(b"x" * 64), max_bytes=32)
        quarantine = Path(self.temp_dir.name) / "artifacts" / "quarantine" / "ART-big"
        clean = Path(self.temp_dir.name) / "artifacts" / "clean" / "ART-big"
        self.assertFalse(quarantine.exists() and any(quarantine.iterdir()))
        self.assertFalse(clean.exists() and any(clean.iterdir()))
        leftovers = list((Path(self.temp_dir.name) / "artifacts" / "tmp").glob("*.part"))
        self.assertEqual(leftovers, [])

    def test_path_traversal_filename_does_not_affect_layout(self) -> None:
        self.assertEqual(safe_filename("../../etc/passwd"), "passwd")
        sha256, _ = self.store.write_quarantine(
            "ART-path", io.BytesIO(self.payload), max_bytes=1024
        )
        expected = Path(self.temp_dir.name) / "artifacts" / "quarantine" / "ART-path" / sha256
        self.assertTrue(expected.is_file())
        self.assertFalse((Path(self.temp_dir.name) / "etc").exists())

    def test_clean_open_cannot_read_quarantine(self) -> None:
        sha256, _ = self.store.write_quarantine("ART-q", io.BytesIO(self.payload), max_bytes=1024)
        with self.assertRaises(ArtifactAccessError), self.store.open_clean("ART-q", sha256):
            pass
        self.store.promote_clean("ART-q", sha256)
        with self.store.open_clean("ART-q", sha256) as handle:
            self.assertEqual(handle.read(), self.payload)

    def test_immutable_body_cannot_be_overwritten(self) -> None:
        sha256, _ = self.store.write_quarantine(
            "ART-immut", io.BytesIO(self.payload), max_bytes=1024
        )
        target = Path(self.temp_dir.name) / "artifacts" / "quarantine" / "ART-immut" / sha256
        target.write_bytes(b"tampered-bytes")
        with self.assertRaises(ArtifactImmutabilityError):
            self.store.write_quarantine("ART-immut", io.BytesIO(self.payload), max_bytes=1024)

    def test_identical_rewrite_is_idempotent(self) -> None:
        first = self.store.write_quarantine("ART-id", io.BytesIO(self.payload), max_bytes=1024)
        second = self.store.write_quarantine("ART-id", io.BytesIO(self.payload), max_bytes=1024)
        self.assertEqual(first, second)

    def test_delete_expired_removes_old_objects(self) -> None:
        sha256, _ = self.store.write_quarantine("ART-old", io.BytesIO(self.payload), max_bytes=1024)
        path = Path(self.temp_dir.name) / "artifacts" / "quarantine" / "ART-old" / sha256
        old = datetime.now(UTC) - timedelta(hours=2)
        os.utime(path, (path.stat().st_atime, old.timestamp()))
        removed = self.store.delete_expired(datetime.now(UTC), max_age_seconds=60)
        self.assertGreaterEqual(removed, 1)
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
