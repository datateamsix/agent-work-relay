from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..contracts import LedgerEntry, WorkAction, WorkKind, WorkOrder, WorkStatus
from .base import WorkOrderSession

_SCHEMA = """
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
    created_at TEXT NOT NULL,
    bundle_sha256 TEXT
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

CREATE INDEX IF NOT EXISTS idx_ledger_work_order
ON ledger(work_order_id, sequence);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_unique_routed
ON ledger(work_order_id) WHERE event_type = 'work_order.routed';

CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_unique_acknowledged
ON ledger(work_order_id) WHERE event_type = 'executor.acknowledged';

CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_unique_plan_received
ON ledger(work_order_id) WHERE event_type = 'plan.received';

CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_unique_plan_available
ON ledger(work_order_id) WHERE event_type = 'plan.available';

CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_unique_bundle_validated
ON ledger(work_order_id) WHERE event_type = 'bundle.validated';
"""

_LIFECYCLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS response_packets (
    packet_id TEXT PRIMARY KEY,
    work_order_id TEXT NOT NULL,
    response_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    in_reply_to TEXT NOT NULL,
    source_input_sha256 TEXT NOT NULL,
    packet_json TEXT NOT NULL,
    receipt_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (actor, response_type, idempotency_key),
    FOREIGN KEY (work_order_id) REFERENCES work_orders(work_order_id)
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    work_order_id TEXT NOT NULL,
    decision_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    target_sha256 TEXT NOT NULL,
    permitted_action TEXT NOT NULL,
    scope TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    expires_at TEXT,
    fingerprint TEXT,
    receipt_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (actor, decision_type, idempotency_key),
    FOREIGN KEY (work_order_id) REFERENCES work_orders(work_order_id)
);

CREATE TABLE IF NOT EXISTS lifecycle_snapshots (
    work_order_id TEXT PRIMARY KEY,
    snapshot_json TEXT NOT NULL,
    FOREIGN KEY (work_order_id) REFERENCES work_orders(work_order_id)
);
"""

_ARTIFACT_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
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
    scan_lease_id TEXT,
    scan_lease_expires_at TEXT,
    scan_attempt INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (owner, idempotency_key)
);

CREATE TABLE IF NOT EXISTS artifact_security_receipts (
    receipt_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL,
    scanner_id TEXT NOT NULL,
    scanner_version TEXT NOT NULL,
    signature_version TEXT NOT NULL,
    verdict TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    scanned_sha256 TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    diagnostics_json TEXT NOT NULL,
    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id)
);

CREATE TABLE IF NOT EXISTS artifact_receipts (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    artifact_id TEXT NOT NULL,
    work_order_id TEXT,
    correlation_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    counterparty TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id)
);

CREATE INDEX IF NOT EXISTS idx_artifact_receipts_artifact
ON artifact_receipts(artifact_id, sequence);

CREATE UNIQUE INDEX IF NOT EXISTS idx_artifact_receipts_declared
ON artifact_receipts(artifact_id) WHERE event_type = 'artifact.declared';

CREATE UNIQUE INDEX IF NOT EXISTS idx_artifact_receipts_quarantined
ON artifact_receipts(artifact_id) WHERE event_type = 'artifact.quarantined';

CREATE UNIQUE INDEX IF NOT EXISTS idx_artifact_receipts_rejected
ON artifact_receipts(artifact_id) WHERE event_type = 'artifact.rejected';

CREATE UNIQUE INDEX IF NOT EXISTS idx_artifact_receipts_scan_started
ON artifact_receipts(artifact_id) WHERE event_type = 'artifact.scan_started';

CREATE UNIQUE INDEX IF NOT EXISTS idx_artifact_receipts_scan_passed
ON artifact_receipts(artifact_id) WHERE event_type = 'artifact.scan_passed';

CREATE UNIQUE INDEX IF NOT EXISTS idx_artifact_receipts_promoted
ON artifact_receipts(artifact_id) WHERE event_type = 'artifact.promoted';

CREATE UNIQUE INDEX IF NOT EXISTS idx_artifact_security_receipts_digest
ON artifact_security_receipts(artifact_id, scanned_sha256);

CREATE UNIQUE INDEX IF NOT EXISTS idx_artifact_receipts_relay_authorized
ON artifact_receipts(artifact_id, work_order_id)
WHERE event_type = 'artifact.relay_authorized';

CREATE TABLE IF NOT EXISTS artifact_upload_tickets (
    ticket_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL UNIQUE,
    owner TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    spent_at TEXT,
    max_bytes INTEGER NOT NULL,
    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id)
);
"""


class SQLiteStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(_SCHEMA)
            self._migrate(connection)

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(work_orders)").fetchall()
        }
        if "repository_url" not in columns:
            connection.execute(
                "ALTER TABLE work_orders ADD COLUMN repository_url TEXT NOT NULL DEFAULT ''"
            )
        if "base_ref" not in columns:
            connection.execute(
                "ALTER TABLE work_orders ADD COLUMN base_ref TEXT NOT NULL DEFAULT 'main'"
            )
        if "bundle_sha256" not in columns:
            connection.execute("ALTER TABLE work_orders ADD COLUMN bundle_sha256 TEXT")
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_unique_routed
            ON ledger(work_order_id) WHERE event_type = 'work_order.routed'
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_unique_acknowledged
            ON ledger(work_order_id) WHERE event_type = 'executor.acknowledged'
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_unique_plan_received
            ON ledger(work_order_id) WHERE event_type = 'plan.received'
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_unique_plan_available
            ON ledger(work_order_id) WHERE event_type = 'plan.available'
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_unique_bundle_validated
            ON ledger(work_order_id) WHERE event_type = 'bundle.validated'
            """
        )
        connection.executescript(_ARTIFACT_SCHEMA)
        SQLiteStateStore._migrate_artifact_columns(connection)
        connection.executescript(_LIFECYCLE_SCHEMA)
        SQLiteStateStore._migrate_lifecycle_columns(connection)

    @staticmethod
    def _migrate_artifact_columns(connection: sqlite3.Connection) -> None:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "artifacts" not in tables:
            return
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(artifacts)").fetchall()
        }
        if "scan_lease_id" not in columns:
            connection.execute("ALTER TABLE artifacts ADD COLUMN scan_lease_id TEXT")
        if "scan_lease_expires_at" not in columns:
            connection.execute("ALTER TABLE artifacts ADD COLUMN scan_lease_expires_at TEXT")
        if "scan_attempt" not in columns:
            connection.execute(
                "ALTER TABLE artifacts ADD COLUMN scan_attempt INTEGER NOT NULL DEFAULT 0"
            )

    @staticmethod
    def _migrate_lifecycle_columns(connection: sqlite3.Connection) -> None:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "response_packets" in tables:
            packet_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(response_packets)").fetchall()
            }
            if "receipt_json" not in packet_columns:
                connection.execute("ALTER TABLE response_packets ADD COLUMN receipt_json TEXT")
        if "decisions" in tables:
            decision_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(decisions)").fetchall()
            }
            if "rationale" not in decision_columns:
                connection.execute(
                    "ALTER TABLE decisions ADD COLUMN rationale TEXT NOT NULL DEFAULT ''"
                )
            if "expires_at" not in decision_columns:
                connection.execute("ALTER TABLE decisions ADD COLUMN expires_at TEXT")
            if "fingerprint" not in decision_columns:
                connection.execute("ALTER TABLE decisions ADD COLUMN fingerprint TEXT")
            if "receipt_json" not in decision_columns:
                connection.execute("ALTER TABLE decisions ADD COLUMN receipt_json TEXT")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_work_order(self, work_order: WorkOrder) -> tuple[WorkOrder, bool, int]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM work_orders WHERE idempotency_key = ?",
                (work_order.idempotency_key,),
            ).fetchone()
            if existing is not None:
                sequence = connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) FROM ledger WHERE work_order_id = ?",
                    (existing["work_order_id"],),
                ).fetchone()[0]
                return self._row_to_work_order(existing), False, int(sequence)

            connection.execute(
                """
                INSERT INTO work_orders (
                    work_order_id, idempotency_key, sender, recipient, kind, action,
                    parent_work_order_id, repository_url, base_ref, markdown,
                    content_sha256, wrapper_id, wrapper_version, wrapper_sha256,
                    status, created_at, bundle_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    work_order.work_order_id,
                    work_order.idempotency_key,
                    work_order.sender,
                    work_order.recipient,
                    work_order.kind.value,
                    work_order.action.value,
                    work_order.parent_work_order_id,
                    work_order.repository_url,
                    work_order.base_ref,
                    work_order.markdown,
                    work_order.content_sha256,
                    work_order.wrapper_id,
                    work_order.wrapper_version,
                    work_order.wrapper_sha256,
                    work_order.status.value,
                    work_order.created_at,
                    work_order.bundle_sha256,
                ),
            )
            accepted_payload = {
                "content_sha256": work_order.content_sha256,
                "wrapper": f"{work_order.wrapper_id}@{work_order.wrapper_version}",
            }
            if work_order.bundle_sha256 is not None:
                accepted_payload["bundle_sha256"] = work_order.bundle_sha256
            sequence = self._append_ledger_on_connection(
                connection=connection,
                work_order_id=work_order.work_order_id,
                event_type="work_order.accepted",
                actor=work_order.sender,
                counterparty="broker:awr",
                payload=accepted_payload,
            ).sequence
            return work_order, True, sequence

    def get_work_order(self, work_order_id: str) -> WorkOrder | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM work_orders WHERE work_order_id = ?", (work_order_id,)
            ).fetchone()
            return None if row is None else self._row_to_work_order(row)

    def list_work_orders(self) -> list[WorkOrder]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM work_orders ORDER BY created_at").fetchall()
            return [self._row_to_work_order(row) for row in rows]

    def get_by_idempotency_key(self, idempotency_key: str) -> WorkOrder | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM work_orders WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            return None if row is None else self._row_to_work_order(row)

    def update_status(self, work_order_id: str, status: WorkStatus) -> None:
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE work_orders SET status = ? WHERE work_order_id = ?",
                (status.value, work_order_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown work order: {work_order_id}")

    def append_ledger(
        self,
        work_order_id: str,
        event_type: str,
        actor: str,
        counterparty: str,
        payload: dict[str, Any],
    ) -> LedgerEntry:
        with self._connection() as connection:
            return self._append_ledger_on_connection(
                connection, work_order_id, event_type, actor, counterparty, payload
            )

    def _append_ledger_on_connection(
        self,
        connection: sqlite3.Connection,
        work_order_id: str,
        event_type: str,
        actor: str,
        counterparty: str,
        payload: dict[str, Any],
    ) -> LedgerEntry:
        event_id = f"evt-{uuid4()}"
        cursor = connection.execute(
            """
            INSERT INTO ledger (
                event_id, work_order_id, event_type, actor, counterparty, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                work_order_id,
                event_type,
                actor,
                counterparty,
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
            ),
        )
        row = connection.execute(
            "SELECT * FROM ledger WHERE sequence = ?", (cursor.lastrowid,)
        ).fetchone()
        assert row is not None
        return self._row_to_ledger(row)

    def list_ledger(self, work_order_id: str | None = None) -> list[LedgerEntry]:
        with self._connection() as connection:
            if work_order_id is None:
                rows = connection.execute("SELECT * FROM ledger ORDER BY sequence").fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM ledger WHERE work_order_id = ? ORDER BY sequence",
                    (work_order_id,),
                ).fetchall()
            return [self._row_to_ledger(row) for row in rows]

    @contextmanager
    def lock_work_order(self, work_order_id: str) -> Iterator[WorkOrderSession]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM work_orders WHERE work_order_id = ?",
                (work_order_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown work order: {work_order_id}")
            yield _SQLiteWorkOrderSession(self, connection, work_order_id)

    @staticmethod
    def _row_to_work_order(row: sqlite3.Row) -> WorkOrder:
        return WorkOrder(
            work_order_id=row["work_order_id"],
            idempotency_key=row["idempotency_key"],
            sender=row["sender"],
            recipient=row["recipient"],
            kind=WorkKind(row["kind"]),
            action=WorkAction(row["action"]),
            parent_work_order_id=row["parent_work_order_id"],
            repository_url=row["repository_url"],
            base_ref=row["base_ref"],
            markdown=row["markdown"],
            content_sha256=row["content_sha256"],
            wrapper_id=row["wrapper_id"],
            wrapper_version=row["wrapper_version"],
            wrapper_sha256=row["wrapper_sha256"],
            status=WorkStatus(row["status"]),
            created_at=row["created_at"],
            bundle_sha256=_optional_work_order_str(row, "bundle_sha256"),
        )

    @staticmethod
    def _row_to_ledger(row: sqlite3.Row) -> LedgerEntry:
        return LedgerEntry(
            sequence=int(row["sequence"]),
            event_id=row["event_id"],
            work_order_id=row["work_order_id"],
            event_type=row["event_type"],
            actor=row["actor"],
            counterparty=row["counterparty"],
            payload=json.loads(row["payload_json"]),
            created_at=row["created_at"],
        )


def _optional_work_order_str(row: sqlite3.Row, name: str) -> str | None:
    try:
        value = row[name]
    except (IndexError, KeyError):
        return None
    if value is None:
        return None
    return str(value)


class _SQLiteWorkOrderSession:
    def __init__(
        self,
        store: SQLiteStateStore,
        connection: sqlite3.Connection,
        work_order_id: str,
    ) -> None:
        self._store = store
        self._connection = connection
        self._work_order_id = work_order_id

    def get_work_order(self) -> WorkOrder:
        row = self._connection.execute(
            "SELECT * FROM work_orders WHERE work_order_id = ?",
            (self._work_order_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown work order: {self._work_order_id}")
        return SQLiteStateStore._row_to_work_order(row)

    def list_ledger(self) -> list[LedgerEntry]:
        rows = self._connection.execute(
            "SELECT * FROM ledger WHERE work_order_id = ? ORDER BY sequence",
            (self._work_order_id,),
        ).fetchall()
        return [SQLiteStateStore._row_to_ledger(row) for row in rows]

    def update_status(self, status: WorkStatus) -> None:
        cursor = self._connection.execute(
            "UPDATE work_orders SET status = ? WHERE work_order_id = ?",
            (status.value, self._work_order_id),
        )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown work order: {self._work_order_id}")

    def append_ledger(
        self,
        event_type: str,
        actor: str,
        counterparty: str,
        payload: dict[str, Any],
    ) -> LedgerEntry:
        return self._store._append_ledger_on_connection(
            self._connection,
            self._work_order_id,
            event_type,
            actor,
            counterparty,
            payload,
        )

    def put_response_packet(self, packet: dict[str, Any]) -> None:
        self._connection.execute(
            """
            INSERT INTO response_packets (
                packet_id, work_order_id, response_type, actor, idempotency_key,
                content_sha256, in_reply_to, source_input_sha256, packet_json, receipt_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(packet["packet_id"]),
                self._work_order_id,
                str(packet["response_type"]),
                str(packet["actor"]),
                str(packet["idempotency_key"]),
                str(packet["content_sha256"]),
                str(packet["in_reply_to"]),
                str(packet["source_input_sha256"]),
                json.dumps(packet["packet"], sort_keys=True, separators=(",", ":")),
                json.dumps(packet.get("receipt"), sort_keys=True, separators=(",", ":"))
                if packet.get("receipt") is not None
                else None,
                str(packet["created_at"]),
            ),
        )

    def get_response_by_idempotency(
        self, actor: str, response_type: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        row = self._connection.execute(
            """
            SELECT packet_json, content_sha256, receipt_json FROM response_packets
            WHERE work_order_id = ? AND actor = ? AND response_type = ? AND idempotency_key = ?
            """,
            (self._work_order_id, actor, response_type, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        receipt = None
        if row["receipt_json"]:
            loaded = json.loads(row["receipt_json"])
            receipt = loaded if isinstance(loaded, dict) else None
        return {
            "packet": json.loads(row["packet_json"]),
            "content_sha256": row["content_sha256"],
            "receipt": receipt,
        }

    def put_decision(self, decision: dict[str, Any]) -> None:
        self._connection.execute(
            """
            INSERT INTO decisions (
                decision_id, work_order_id, decision_type, actor, target_kind, target_id,
                target_sha256, permitted_action, scope, idempotency_key, rationale,
                expires_at, fingerprint, receipt_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(decision["decision_id"]),
                self._work_order_id,
                str(decision["decision_type"]),
                str(decision["actor"]),
                str(decision["target_kind"]),
                str(decision["target_id"]),
                str(decision["target_sha256"]),
                str(decision["permitted_action"]),
                str(decision["scope"]),
                str(decision["idempotency_key"]),
                str(decision.get("rationale") or ""),
                decision.get("expires_at"),
                decision.get("fingerprint"),
                json.dumps(decision.get("receipt"), sort_keys=True, separators=(",", ":"))
                if decision.get("receipt") is not None
                else None,
                str(decision["created_at"]),
            ),
        )

    def list_decisions(self) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM decisions WHERE work_order_id = ? ORDER BY created_at",
            (self._work_order_id,),
        ).fetchall()
        return [
            {
                "decision_id": row["decision_id"],
                "decision_type": row["decision_type"],
                "work_order_id": row["work_order_id"],
                "actor": row["actor"],
                "target_kind": row["target_kind"],
                "target_id": row["target_id"],
                "target_sha256": row["target_sha256"],
                "permitted_action": row["permitted_action"],
                "scope": row["scope"],
                "created_at": row["created_at"],
                "idempotency_key": row["idempotency_key"],
                "rationale": row["rationale"] or "",
                "expires_at": row["expires_at"],
                "fingerprint": row["fingerprint"],
                "receipt": json.loads(row["receipt_json"]) if row["receipt_json"] else None,
            }
            for row in rows
        ]

    def put_lifecycle(self, snapshot: dict[str, Any]) -> None:
        encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        self._connection.execute(
            """
            INSERT INTO lifecycle_snapshots (work_order_id, snapshot_json)
            VALUES (?, ?)
            ON CONFLICT(work_order_id) DO UPDATE SET snapshot_json = excluded.snapshot_json
            """,
            (self._work_order_id, encoded),
        )

    def get_lifecycle(self) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT snapshot_json FROM lifecycle_snapshots WHERE work_order_id = ?",
            (self._work_order_id,),
        ).fetchone()
        if row is None:
            return None
        loaded = json.loads(row["snapshot_json"])
        return loaded if isinstance(loaded, dict) else None
