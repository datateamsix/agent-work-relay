from __future__ import annotations

import hashlib
import io
import tempfile
import threading
import unittest
from pathlib import Path

from awr.artifacts.contracts import ArtifactPurpose, ArtifactStatus
from awr.artifacts.errors import ArtifactError
from awr.artifacts.service import ArtifactService
from awr.storage.artifact_fs import LocalArtifactBodyStore
from awr.storage.artifact_sqlite import SQLiteArtifactMetadataStore


class ArtifactConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.service = ArtifactService(
            SQLiteArtifactMetadataStore(root / "awr.db"),
            LocalArtifactBodyStore(root / "artifacts"),
        )
        self.payload = b"concurrent-bytes"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_concurrent_declare_returns_one_artifact(self) -> None:
        results: list[str] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(8)

        def worker() -> None:
            try:
                barrier.wait()
                artifact, _created = self.service.declare(
                    owner="chatgpt:planner",
                    original_filename="spec.md",
                    declared_media_type="text/markdown",
                    purpose=ArtifactPurpose.REQUIREMENTS_REFERENCE,
                    idempotency_key="concurrent-declare",
                )
                results.append(artifact.artifact_id)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(set(results)), 1)
        receipts = self.service.metadata.list_receipts(results[0])
        self.assertEqual([entry.event_type for entry in receipts], ["artifact.declared"])

    def test_concurrent_finalization_does_not_create_conflicting_versions(self) -> None:
        artifact, _ = self.service.declare(
            owner="chatgpt:planner",
            original_filename="spec.md",
            declared_media_type="text/markdown",
            purpose=ArtifactPurpose.REQUIREMENTS_REFERENCE,
            idempotency_key="concurrent-finalize",
        )
        results: list[str] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(8)

        def worker() -> None:
            try:
                barrier.wait()
                finalized = self.service.finalize_stream(
                    artifact.artifact_id, io.BytesIO(self.payload)
                )
                assert finalized.sha256 is not None
                results.append(finalized.sha256)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        expected = hashlib.sha256(self.payload).hexdigest()
        self.assertEqual(set(results), {expected})
        loaded = self.service.metadata.get(artifact.artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.QUARANTINED)
        self.assertEqual(loaded.sha256, expected)
        events = [
            entry.event_type for entry in self.service.metadata.list_receipts(artifact.artifact_id)
        ]
        self.assertEqual(events.count("artifact.quarantined"), 1)

    def test_concurrent_conflicting_bytes_leave_one_generation(self) -> None:
        artifact, _ = self.service.declare(
            owner="chatgpt:planner",
            original_filename="spec.md",
            declared_media_type="text/markdown",
            purpose=ArtifactPurpose.REQUIREMENTS_REFERENCE,
            idempotency_key="concurrent-conflict",
        )
        payloads = [b"alpha-bytes", b"beta-bytes-xx"]
        winners: list[str] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(2)

        def worker(payload: bytes) -> None:
            try:
                barrier.wait()
                finalized = self.service.finalize_stream(artifact.artifact_id, io.BytesIO(payload))
                assert finalized.sha256 is not None
                winners.append(finalized.sha256)
            except ArtifactError as exc:
                errors.append(exc)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(payloads[0],)),
            threading.Thread(target=worker, args=(payloads[1],)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        loaded = self.service.metadata.get(artifact.artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.QUARANTINED)
        self.assertIsNotNone(loaded.sha256)
        self.assertEqual(
            len({hashlib.sha256(item).hexdigest() for item in payloads} & {loaded.sha256}), 1
        )
        self.assertEqual(
            [
                entry.event_type
                for entry in self.service.metadata.list_receipts(artifact.artifact_id)
            ].count("artifact.quarantined"),
            1,
        )


if __name__ == "__main__":
    unittest.main()
