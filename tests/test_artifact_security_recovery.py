from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from artifact_fixtures import FakeClock

from awr.artifacts.contracts import (
    ArtifactPurpose,
    ArtifactSecurityReceipt,
    ArtifactSecurityVerdict,
    ArtifactStatus,
)
from awr.artifacts.retention import ArtifactRetention
from awr.artifacts.scan import CleanScanner, ScanResult
from awr.artifacts.security import ArtifactSecurityService
from awr.artifacts.service import ArtifactService
from awr.storage.artifact_fs import LocalArtifactBodyStore
from awr.storage.artifact_sqlite import SQLiteArtifactMetadataStore
from awr.storage.quarantine_only import QuarantineOnlyBodyStore


class _FailIfCalledScanner:
    def scan(self, payload: bytes) -> ScanResult:
        del payload
        raise AssertionError("scanner must not run during receipt recovery")


class ArtifactSecurityRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_path = root / "awr.db"
        self.metadata = SQLiteArtifactMetadataStore(self.db_path)
        self.bodies = LocalArtifactBodyStore(root / "artifacts")
        self.intake = ArtifactService(
            self.metadata, QuarantineOnlyBodyStore(self.bodies), max_bytes=1024
        )
        self.clock = FakeClock(datetime.now(UTC))
        self.security = ArtifactSecurityService(
            self.metadata,
            self.bodies,
            CleanScanner(),
            clock=self.clock,
            max_bytes=1024,
            lease_ttl_seconds=30,
        )
        artifact, _ = self.intake.declare(
            owner="chatgpt:planner",
            original_filename="notes.txt",
            declared_media_type="text/plain",
            purpose=ArtifactPurpose.OTHER_REFERENCE,
            idempotency_key="recover",
        )
        self.payload = b"recover-me\n"
        finalized = self.intake.finalize_stream(artifact.artifact_id, io.BytesIO(self.payload))
        self.artifact_id = artifact.artifact_id
        assert finalized.sha256 is not None
        self.digest = finalized.sha256

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_stale_lease_is_reclaimed(self) -> None:
        past = (self.clock.now() - timedelta(seconds=120)).isoformat()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE artifacts
                SET status = ?, scan_lease_id = ?, scan_lease_expires_at = ?, scan_attempt = 1
                WHERE artifact_id = ?
                """,
                (ArtifactStatus.SCANNING.value, "stale-lease", past, self.artifact_id),
            )
            connection.commit()
        receipt = self.security.inspect(self.artifact_id)
        loaded = self.metadata.get(self.artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.CLEAN)
        self.assertEqual(receipt.verdict, ArtifactSecurityVerdict.CLEAN)
        self.assertGreaterEqual(loaded.scan_attempt, 2)

    def test_fingerprint_mismatch_is_tampering(self) -> None:
        target = self.bodies.quarantine_root / self.artifact_id / self.digest
        target.write_bytes(b"tampered-generation")
        self.security.inspect(self.artifact_id)
        loaded = self.metadata.get(self.artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.REJECTED_TAMPERING)
        self.assertFalse(self.bodies.has_clean(self.artifact_id, self.digest))

    def test_missing_quarantine_fails_closed_as_tampering(self) -> None:
        self.bodies.delete_generation(self.artifact_id, self.digest)
        self.security.inspect(self.artifact_id)
        loaded = self.metadata.get(self.artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.REJECTED_TAMPERING)
        self.assertFalse(self.bodies.has_clean(self.artifact_id, self.digest))

    def test_complete_scan_follows_rejected_receipt_not_caller_status(self) -> None:
        now = datetime.now(UTC).isoformat()
        past = (self.clock.now() - timedelta(seconds=120)).isoformat()
        self.metadata.put_security_receipt(
            ArtifactSecurityReceipt(
                receipt_id=f"scr-{uuid4()}",
                artifact_id=self.artifact_id,
                scanner_id="retention",
                scanner_version="1",
                signature_version="0",
                verdict=ArtifactSecurityVerdict.UNAVAILABLE,
                reason_codes=("scanner_unavailable",),
                scanned_sha256=self.digest,
                started_at=now,
                completed_at=now,
                diagnostics={
                    "artifact_status": ArtifactStatus.REJECTED_SCANNER_UNAVAILABLE.value,
                },
            )
        )
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE artifacts
                SET status = ?, scan_lease_id = ?, scan_lease_expires_at = ?
                WHERE artifact_id = ?
                """,
                (ArtifactStatus.SCANNING.value, "other", past, self.artifact_id),
            )
            connection.commit()
        completed = self.metadata.complete_scan(
            self.artifact_id,
            lease_id="attacker",
            status=ArtifactStatus.CLEAN,
            detected_media_type="text/plain",
            now=self.clock.now(),
        )
        self.assertEqual(completed.status, ArtifactStatus.REJECTED_SCANNER_UNAVAILABLE)
        self.assertFalse(self.bodies.has_clean(self.artifact_id, self.digest))

    def test_retention_completes_stuck_scanning_from_existing_receipt(self) -> None:
        now = datetime.now(UTC).isoformat()
        past = (self.clock.now() - timedelta(seconds=120)).isoformat()
        self.metadata.put_security_receipt(
            ArtifactSecurityReceipt(
                receipt_id=f"scr-{uuid4()}",
                artifact_id=self.artifact_id,
                scanner_id="fake-clean",
                scanner_version="1",
                signature_version="1",
                verdict=ArtifactSecurityVerdict.CLEAN,
                reason_codes=(),
                scanned_sha256=self.digest,
                started_at=now,
                completed_at=now,
                diagnostics={
                    "artifact_status": ArtifactStatus.CLEAN.value,
                    "detected_media_type": "text/plain",
                },
            )
        )
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE artifacts
                SET status = ?, scan_lease_id = ?, scan_lease_expires_at = ?
                WHERE artifact_id = ?
                """,
                (ArtifactStatus.SCANNING.value, "crashed", past, self.artifact_id),
            )
            connection.commit()
        ArtifactRetention(
            self.metadata,
            self.bodies,
            clock=self.clock,
            declare_ttl_seconds=86400,
            clean_ttl_seconds=604800,
        ).purge()
        loaded = self.metadata.get(self.artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.CLEAN)
        self.assertTrue(self.bodies.has_clean(self.artifact_id, self.digest))

    def test_complete_scan_can_fail_closed_over_unpromotable_clean_receipt(self) -> None:
        now = datetime.now(UTC).isoformat()
        past = (self.clock.now() - timedelta(seconds=120)).isoformat()
        self.metadata.put_security_receipt(
            ArtifactSecurityReceipt(
                receipt_id=f"scr-{uuid4()}",
                artifact_id=self.artifact_id,
                scanner_id="fake-clean",
                scanner_version="1",
                signature_version="1",
                verdict=ArtifactSecurityVerdict.CLEAN,
                reason_codes=(),
                scanned_sha256=self.digest,
                started_at=now,
                completed_at=now,
                diagnostics={"artifact_status": ArtifactStatus.CLEAN.value},
            )
        )
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE artifacts
                SET status = ?, scan_lease_id = ?, scan_lease_expires_at = ?
                WHERE artifact_id = ?
                """,
                (ArtifactStatus.SCANNING.value, "crashed", past, self.artifact_id),
            )
            connection.commit()
        completed = self.metadata.complete_scan(
            self.artifact_id,
            lease_id="retention",
            status=ArtifactStatus.REJECTED_TAMPERING,
            detected_media_type="text/plain",
            now=self.clock.now(),
        )
        self.assertEqual(completed.status, ArtifactStatus.REJECTED_TAMPERING)
        self.assertFalse(self.bodies.has_clean(self.artifact_id, self.digest))

    def test_retention_fail_closes_clean_receipt_when_quarantine_is_gone(self) -> None:
        now = datetime.now(UTC).isoformat()
        past = (self.clock.now() - timedelta(seconds=120)).isoformat()
        self.metadata.put_security_receipt(
            ArtifactSecurityReceipt(
                receipt_id=f"scr-{uuid4()}",
                artifact_id=self.artifact_id,
                scanner_id="fake-clean",
                scanner_version="1",
                signature_version="1",
                verdict=ArtifactSecurityVerdict.CLEAN,
                reason_codes=(),
                scanned_sha256=self.digest,
                started_at=now,
                completed_at=now,
                diagnostics={"artifact_status": ArtifactStatus.CLEAN.value},
            )
        )
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE artifacts
                SET status = ?, scan_lease_id = ?, scan_lease_expires_at = ?
                WHERE artifact_id = ?
                """,
                (ArtifactStatus.SCANNING.value, "crashed", past, self.artifact_id),
            )
            connection.commit()
        self.bodies.delete_generation(self.artifact_id, self.digest)
        ArtifactRetention(
            self.metadata,
            self.bodies,
            clock=self.clock,
            declare_ttl_seconds=86400,
            clean_ttl_seconds=604800,
        ).purge()
        loaded = self.metadata.get(self.artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.REJECTED_TAMPERING)
        self.assertFalse(self.bodies.has_clean(self.artifact_id, self.digest))

    def test_inspect_repairs_clean_status_without_promoted_body(self) -> None:
        now = datetime.now(UTC).isoformat()
        self.metadata.put_security_receipt(
            ArtifactSecurityReceipt(
                receipt_id=f"scr-{uuid4()}",
                artifact_id=self.artifact_id,
                scanner_id="fake-clean",
                scanner_version="1",
                signature_version="1",
                verdict=ArtifactSecurityVerdict.CLEAN,
                reason_codes=(),
                scanned_sha256=self.digest,
                started_at=now,
                completed_at=now,
                diagnostics={
                    "artifact_status": ArtifactStatus.CLEAN.value,
                    "detected_media_type": "text/plain",
                    "control_authority": "primary_markdown_only",
                },
            )
        )
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE artifacts
                SET status = ?, scan_lease_id = NULL, scan_lease_expires_at = NULL
                WHERE artifact_id = ?
                """,
                (ArtifactStatus.CLEAN.value, self.artifact_id),
            )
            connection.commit()
        self.assertFalse(self.bodies.has_clean(self.artifact_id, self.digest))
        receipt = self.security.inspect(self.artifact_id)
        loaded = self.metadata.get(self.artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.CLEAN)
        self.assertEqual(receipt.verdict, ArtifactSecurityVerdict.CLEAN)
        self.assertTrue(self.bodies.has_clean(self.artifact_id, self.digest))

    def test_persisted_receipt_wins_over_local_clean_decision(self) -> None:
        now = datetime.now(UTC).isoformat()
        original = self.metadata.put_security_receipt

        def hijack(receipt: ArtifactSecurityReceipt) -> ArtifactSecurityReceipt:
            rejected = ArtifactSecurityReceipt(
                receipt_id=f"scr-{uuid4()}",
                artifact_id=receipt.artifact_id,
                scanner_id=receipt.scanner_id,
                scanner_version=receipt.scanner_version,
                signature_version=receipt.signature_version,
                verdict=ArtifactSecurityVerdict.UNAVAILABLE,
                reason_codes=("scanner_unavailable",),
                scanned_sha256=receipt.scanned_sha256,
                started_at=now,
                completed_at=now,
                diagnostics={
                    "artifact_status": ArtifactStatus.REJECTED_SCANNER_UNAVAILABLE.value,
                    "control_authority": "primary_markdown_only",
                },
            )
            return original(rejected)

        self.metadata.put_security_receipt = hijack  # type: ignore[method-assign]
        receipt = self.security.inspect(self.artifact_id)
        loaded = self.metadata.get(self.artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.REJECTED_SCANNER_UNAVAILABLE)
        self.assertEqual(receipt.verdict, ArtifactSecurityVerdict.UNAVAILABLE)
        self.assertFalse(self.bodies.has_clean(self.artifact_id, self.digest))

    def test_interrupted_promote_resumes_without_rescan(self) -> None:
        now = datetime.now(UTC).isoformat()
        self.metadata.put_security_receipt(
            ArtifactSecurityReceipt(
                receipt_id=f"scr-{uuid4()}",
                artifact_id=self.artifact_id,
                scanner_id="fake-clean",
                scanner_version="1",
                signature_version="1",
                verdict=ArtifactSecurityVerdict.CLEAN,
                reason_codes=(),
                scanned_sha256=self.digest,
                started_at=now,
                completed_at=now,
                diagnostics={
                    "artifact_status": ArtifactStatus.CLEAN.value,
                    "detected_media_type": "text/plain",
                    "control_authority": "primary_markdown_only",
                },
            )
        )
        past = (self.clock.now() - timedelta(seconds=120)).isoformat()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE artifacts
                SET status = ?, scan_lease_id = ?, scan_lease_expires_at = ?
                WHERE artifact_id = ?
                """,
                (ArtifactStatus.SCANNING.value, "crashed", past, self.artifact_id),
            )
            connection.commit()
        recovering = ArtifactSecurityService(
            self.metadata,
            self.bodies,
            _FailIfCalledScanner(),
            clock=self.clock,
            max_bytes=1024,
        )
        receipt = recovering.inspect(self.artifact_id)
        loaded = self.metadata.get(self.artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.CLEAN)
        self.assertEqual(receipt.verdict, ArtifactSecurityVerdict.CLEAN)
        self.assertTrue(self.bodies.has_clean(self.artifact_id, self.digest))

    def test_retention_deletes_rejected_bodies_and_keeps_receipts(self) -> None:
        self.security.inspect(self.artifact_id)
        loaded = self.metadata.get(self.artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.CLEAN)
        retention = ArtifactRetention(
            self.metadata,
            self.bodies,
            clock=self.clock,
            declare_ttl_seconds=86400,
            clean_ttl_seconds=10,
        )
        self.assertEqual(retention.purge(), 0)
        self.assertTrue(self.bodies.has_clean(self.artifact_id, self.digest))
        self.clock.advance(11)
        removed = retention.purge()
        self.assertGreaterEqual(removed, 1)
        self.assertFalse(self.bodies.has_clean(self.artifact_id, self.digest))
        self.assertFalse(self.bodies.has_quarantine(self.artifact_id, self.digest))
        self.assertEqual(len(self.metadata.list_security_receipts(self.artifact_id)), 1)
        kept = self.metadata.get(self.artifact_id)
        assert kept is not None
        self.assertEqual(kept.status, ArtifactStatus.CLEAN)

    def test_retention_expires_unclaimed_quarantine(self) -> None:
        retention = ArtifactRetention(
            self.metadata,
            self.bodies,
            clock=self.clock,
            declare_ttl_seconds=60,
            clean_ttl_seconds=604800,
        )
        self.assertEqual(retention.purge(), 0)
        self.clock.advance(61)
        retention.purge()
        loaded = self.metadata.get(self.artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.REJECTED_SCANNER_UNAVAILABLE)
        self.assertFalse(self.bodies.has_quarantine(self.artifact_id, self.digest))
        receipts = [entry.event_type for entry in self.metadata.list_receipts(self.artifact_id)]
        self.assertIn("artifact.rejected", receipts)

    def test_expired_scanning_without_receipt_fails_closed(self) -> None:
        past = (self.clock.now() - timedelta(seconds=120)).isoformat()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE artifacts
                SET status = ?, scan_lease_id = ?, scan_lease_expires_at = ?
                WHERE artifact_id = ?
                """,
                (ArtifactStatus.SCANNING.value, "dead", past, self.artifact_id),
            )
            connection.commit()
        retention = ArtifactRetention(
            self.metadata,
            self.bodies,
            clock=self.clock,
            declare_ttl_seconds=86400,
            clean_ttl_seconds=604800,
        )
        retention.purge()
        loaded = self.metadata.get(self.artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.REJECTED_SCANNER_UNAVAILABLE)
        receipts = self.metadata.list_security_receipts(self.artifact_id)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0].verdict, ArtifactSecurityVerdict.UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
