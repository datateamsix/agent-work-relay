from __future__ import annotations

import io
import tempfile
import threading
import unittest
from pathlib import Path

from awr.artifacts.contracts import ArtifactPurpose, ArtifactSecurityVerdict, ArtifactStatus
from awr.artifacts.errors import ArtifactConflictError
from awr.artifacts.scan import CleanScanner, ScanResult
from awr.artifacts.security import ArtifactSecurityService
from awr.artifacts.service import ArtifactService
from awr.storage.artifact_fs import LocalArtifactBodyStore
from awr.storage.artifact_sqlite import SQLiteArtifactMetadataStore
from awr.storage.quarantine_only import QuarantineOnlyBodyStore


class _GatedScanner:
    def __init__(self, started: threading.Event, gate: threading.Event) -> None:
        self.started = started
        self.gate = gate
        self.calls = 0

    def scan(self, payload: bytes) -> ScanResult:
        self.calls += 1
        self.started.set()
        self.gate.wait(timeout=5)
        return CleanScanner().scan(payload)


class ArtifactSecurityConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.metadata = SQLiteArtifactMetadataStore(root / "awr.db")
        self.bodies = LocalArtifactBodyStore(root / "artifacts")
        self.intake = ArtifactService(
            self.metadata, QuarantineOnlyBodyStore(self.bodies), max_bytes=1024
        )
        artifact, _ = self.intake.declare(
            owner="chatgpt:planner",
            original_filename="notes.txt",
            declared_media_type="text/plain",
            purpose=ArtifactPurpose.OTHER_REFERENCE,
            idempotency_key="concurrent-inspect",
        )
        self.intake.finalize_stream(artifact.artifact_id, io.BytesIO(b"shared-bytes\n"))
        self.artifact_id = artifact.artifact_id

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_concurrent_inspect_one_lease_one_clean(self) -> None:
        started = threading.Event()
        gate = threading.Event()
        scanner = _GatedScanner(started, gate)
        security = ArtifactSecurityService(self.metadata, self.bodies, scanner, max_bytes=1024)
        winner: list[object] = []
        errors: list[BaseException] = []
        ready = threading.Barrier(2)

        def first() -> None:
            try:
                ready.wait()
                winner.append(security.inspect(self.artifact_id))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def second() -> None:
            try:
                ready.wait()
                started.wait(timeout=5)
                security.inspect(self.artifact_id)
            except ArtifactConflictError as exc:
                errors.append(exc)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                gate.set()

        threads = [threading.Thread(target=first), threading.Thread(target=second)]
        for thread in threads:
            thread.start()
        self.assertTrue(started.wait(timeout=5))
        for thread in threads:
            thread.join(timeout=5)
        loaded = self.metadata.get(self.artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.CLEAN)
        self.assertEqual(len(winner), 1)
        self.assertTrue(any(isinstance(item, ArtifactConflictError) for item in errors))
        self.assertEqual(scanner.calls, 1)
        receipts = self.metadata.list_security_receipts(self.artifact_id)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0].verdict, ArtifactSecurityVerdict.CLEAN)


if __name__ == "__main__":
    unittest.main()
