from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from awr.artifacts.contracts import ArtifactPurpose, ArtifactStatus
from awr.artifacts.service import ArtifactService
from awr.executors.recording_cursor import RecordingCursorExecutor
from awr.service import BrokerService
from awr.storage.artifact_fs import LocalArtifactBodyStore
from awr.storage.artifact_sqlite import SQLiteArtifactMetadataStore
from awr.storage.sqlite import SQLiteStateStore

_LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_orders (
    work_order_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    kind TEXT NOT NULL,
    action TEXT NOT NULL,
    parent_work_order_id TEXT,
    repository_url TEXT NOT NULL,
    base_ref TEXT NOT NULL,
    markdown TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    wrapper_id TEXT NOT NULL,
    wrapper_version TEXT NOT NULL,
    wrapper_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    work_order_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    counterparty TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (work_order_id) REFERENCES work_orders(work_order_id)
);
"""

FEATURE = """@awr feature.plan

# Feature
"""


class ArtifactMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "legacy.db"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_migrates_legacy_sqlite_and_preserves_golden_path(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.executescript(_LEGACY_SCHEMA)
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertNotIn("artifacts", tables)

        store = SQLiteStateStore(self.db_path)
        service = BrokerService(store, RecordingCursorExecutor())
        receipt = service.submit_prompt_for_planning(
            markdown=FEATURE,
            sender="chatgpt:planner",
            recipient="cursor:backend",
            repository_url="https://github.com/example/project",
            idempotency_key="migration-golden",
        )
        packet = service.refresh_planning(receipt.work_order_id)
        self.assertEqual(packet.work_order_id, receipt.work_order_id)
        events = [entry.event_type for entry in store.list_ledger(receipt.work_order_id)]
        self.assertEqual(
            events,
            [
                "work_order.accepted",
                "work_order.routed",
                "executor.acknowledged",
                "plan.received",
                "plan.available",
            ],
        )

        artifacts = ArtifactService(
            SQLiteArtifactMetadataStore(self.db_path),
            LocalArtifactBodyStore(Path(self.temp_dir.name) / "artifacts"),
        )
        artifact, created = artifacts.declare(
            owner="chatgpt:planner",
            original_filename="note.txt",
            declared_media_type="text/plain",
            purpose=ArtifactPurpose.OTHER_REFERENCE,
            idempotency_key="migration-artifact",
        )
        self.assertTrue(created)
        quarantined = artifacts.finalize_stream(artifact.artifact_id, io.BytesIO(b"migrated"))
        self.assertEqual(quarantined.status, ArtifactStatus.QUARANTINED)
        with sqlite3.connect(self.db_path) as connection:
            names = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        self.assertIn("artifacts", names)
        self.assertIn("artifact_receipts", names)
        self.assertIn("artifact_security_receipts", names)
        columns = {
            str(row[1])
            for row in sqlite3.connect(self.db_path).execute("PRAGMA table_info(artifacts)")
        }
        self.assertIn("scan_lease_id", columns)
        self.assertIn("scan_lease_expires_at", columns)
        self.assertIn("scan_attempt", columns)

    def test_migrates_lease_columns_onto_as01_schema(self) -> None:
        as01 = """
        CREATE TABLE artifacts (
            artifact_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL,
            owner TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            declared_media_type TEXT NOT NULL,
            detected_media_type TEXT,
            byte_length INTEGER,
            sha256 TEXT,
            purpose TEXT NOT NULL,
            status TEXT NOT NULL,
            parent_artifact_id TEXT,
            correlation_id TEXT NOT NULL,
            expected_byte_length INTEGER,
            expected_sha256 TEXT,
            created_at TEXT NOT NULL,
            UNIQUE (owner, idempotency_key)
        );
        """
        with sqlite3.connect(self.db_path) as connection:
            connection.executescript(_LEGACY_SCHEMA)
            connection.executescript(as01)
        SQLiteStateStore(self.db_path)
        with sqlite3.connect(self.db_path) as connection:
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(artifacts)").fetchall()
            }
            indexes = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            work_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(work_orders)").fetchall()
            }
        self.assertIn("scan_lease_id", columns)
        self.assertIn("scan_attempt", columns)
        self.assertIn("idx_artifact_receipts_scan_started", indexes)
        self.assertIn("idx_artifact_security_receipts_digest", indexes)
        self.assertIn("artifact_upload_tickets", tables)
        self.assertIn("bundle_sha256", work_columns)
        self.assertIn("idx_ledger_unique_bundle_validated", indexes)
        self.assertIn("idx_artifact_receipts_relay_authorized", indexes)


if __name__ == "__main__":
    unittest.main()
