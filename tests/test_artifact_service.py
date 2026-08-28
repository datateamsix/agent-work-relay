from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from awr.artifacts.contracts import (
    ArtifactPurpose,
    ArtifactSecurityReceipt,
    ArtifactSecurityVerdict,
    ArtifactStatus,
)
from awr.artifacts.errors import ArtifactAccessError, ArtifactError, ArtifactTooLargeError
from awr.artifacts.service import ArtifactService
from awr.storage.artifact_fs import LocalArtifactBodyStore
from awr.storage.artifact_sqlite import SQLiteArtifactMetadataStore


class ArtifactServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.service = ArtifactService(
            SQLiteArtifactMetadataStore(root / "awr.db"),
            LocalArtifactBodyStore(root / "artifacts"),
            max_bytes=64,
        )
        self.payload = b"hello-artifact"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_declare_and_quarantine_receipts(self) -> None:
        artifact, created = self.service.declare(
            owner="chatgpt:planner",
            original_filename="notes.txt",
            declared_media_type="text/plain",
            purpose=ArtifactPurpose.OTHER_REFERENCE,
            idempotency_key="art-1",
        )
        self.assertTrue(created)
        self.assertEqual(artifact.status, ArtifactStatus.DECLARED)
        finalized = self.service.finalize_stream(artifact.artifact_id, io.BytesIO(self.payload))
        self.assertEqual(finalized.status, ArtifactStatus.QUARANTINED)
        self.assertEqual(finalized.sha256, hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(finalized.byte_length, len(self.payload))
        events = [
            entry.event_type for entry in self.service.metadata.list_receipts(artifact.artifact_id)
        ]
        self.assertEqual(events, ["artifact.declared", "artifact.quarantined"])
        quarantined = self.service.metadata.list_receipts(artifact.artifact_id)[1]
        self.assertEqual(quarantined.payload["sha256"], finalized.sha256)
        self.assertNotIn("path", quarantined.payload)
        self.assertTrue(quarantined.work_order_id is None)
        with (
            self.assertRaises(ArtifactAccessError),
            self.service.bodies.open_clean(artifact.artifact_id, finalized.sha256 or ""),
        ):
            pass

    def test_idempotency_key_returns_same_artifact(self) -> None:
        first, created = self.service.declare(
            owner="chatgpt:planner",
            original_filename="schema.json",
            declared_media_type="application/json",
            purpose="data_contract",
            idempotency_key="same-key",
        )
        self.assertTrue(created)
        replay, created_again = self.service.declare(
            owner="chatgpt:planner",
            original_filename="schema.json",
            declared_media_type="application/json",
            purpose=ArtifactPurpose.DATA_CONTRACT,
            idempotency_key="same-key",
        )
        self.assertFalse(created_again)
        self.assertEqual(first.artifact_id, replay.artifact_id)
        receipts = self.service.metadata.list_receipts(first.artifact_id)
        self.assertEqual([entry.event_type for entry in receipts], ["artifact.declared"])

    def test_rebound_idempotency_key_fails_closed(self) -> None:
        self.service.declare(
            owner="chatgpt:planner",
            original_filename="a.txt",
            declared_media_type="text/plain",
            purpose=ArtifactPurpose.OTHER_REFERENCE,
            idempotency_key="bound",
        )
        with self.assertRaisesRegex(ArtifactError, "already bound"):
            self.service.declare(
                owner="chatgpt:planner",
                original_filename="b.txt",
                declared_media_type="text/plain",
                purpose=ArtifactPurpose.OTHER_REFERENCE,
                idempotency_key="bound",
            )

    def test_oversize_fails_closed_without_clean_object(self) -> None:
        artifact, _ = self.service.declare(
            owner="chatgpt:planner",
            original_filename="big.bin",
            declared_media_type="application/octet-stream",
            purpose=ArtifactPurpose.OTHER_REFERENCE,
            idempotency_key="big",
        )
        with self.assertRaises(ArtifactTooLargeError):
            self.service.finalize_stream(artifact.artifact_id, io.BytesIO(b"x" * 128))
        loaded = self.service.metadata.get(artifact.artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.REJECTED_SIZE)
        events = [
            entry.event_type for entry in self.service.metadata.list_receipts(artifact.artifact_id)
        ]
        self.assertEqual(events, ["artifact.declared", "artifact.rejected"])

    def test_path_traversal_filename_is_metadata_only(self) -> None:
        artifact, _ = self.service.declare(
            owner="chatgpt:planner",
            original_filename="../../etc/passwd",
            declared_media_type="text/plain",
            purpose=ArtifactPurpose.OTHER_REFERENCE,
            idempotency_key="traverse",
        )
        finalized = self.service.finalize_stream(artifact.artifact_id, io.BytesIO(self.payload))
        quarantine_root = Path(self.temp_dir.name) / "artifacts" / "quarantine"
        stored = list(quarantine_root.rglob("*"))
        self.assertTrue(any(path.name == finalized.sha256 for path in stored if path.is_file()))
        self.assertFalse((Path(self.temp_dir.name) / "etc").exists())

    def test_expected_digest_mismatch_is_tampering(self) -> None:
        artifact, _ = self.service.declare(
            owner="chatgpt:planner",
            original_filename="notes.txt",
            declared_media_type="text/plain",
            purpose=ArtifactPurpose.REQUIREMENTS_REFERENCE,
            idempotency_key="tamper",
            expected_sha256="0" * 64,
        )
        with self.assertRaisesRegex(ArtifactError, "declared SHA-256"):
            self.service.finalize_stream(artifact.artifact_id, io.BytesIO(self.payload))
        loaded = self.service.metadata.get(artifact.artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.REJECTED_TAMPERING)

    def test_unknown_purpose_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown artifact purpose"):
            self.service.declare(
                owner="chatgpt:planner",
                original_filename="x.txt",
                declared_media_type="text/plain",
                purpose="grant_execute",
                idempotency_key="bad-purpose",
            )

    def test_security_receipt_round_trip_without_scanner(self) -> None:
        artifact, _ = self.service.declare(
            owner="chatgpt:planner",
            original_filename="notes.txt",
            declared_media_type="text/plain",
            purpose=ArtifactPurpose.OTHER_REFERENCE,
            idempotency_key="receipt",
        )
        now = datetime.now(UTC).isoformat()
        receipt = ArtifactSecurityReceipt(
            receipt_id=f"scr-{uuid4()}",
            artifact_id=artifact.artifact_id,
            scanner_id="none",
            scanner_version="0",
            signature_version="0",
            verdict=ArtifactSecurityVerdict.UNAVAILABLE,
            reason_codes=("not_scanned",),
            scanned_sha256="0" * 64,
            started_at=now,
            completed_at=now,
            diagnostics={"note": "as-01 schema only"},
        )
        self.service.metadata.put_security_receipt(receipt)
        stored = self.service.metadata.list_security_receipts(artifact.artifact_id)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].verdict, ArtifactSecurityVerdict.UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
