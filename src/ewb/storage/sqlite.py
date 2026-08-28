from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..contracts import LedgerEntry, WorkAction, WorkKind, WorkOrder, WorkStatus

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

CREATE INDEX IF NOT EXISTS idx_ledger_work_order
ON ledger(work_order_id, sequence);
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

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
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
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )
            sequence = self._append_ledger_on_connection(
                connection=connection,
                work_order_id=work_order.work_order_id,
                event_type="work_order.accepted",
                actor=work_order.sender,
                counterparty="broker:ewb",
                payload={
                    "content_sha256": work_order.content_sha256,
                    "wrapper": f"{work_order.wrapper_id}@{work_order.wrapper_version}",
                },
            ).sequence
            return work_order, True, sequence

    def get_work_order(self, work_order_id: str) -> WorkOrder | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM work_orders WHERE work_order_id = ?", (work_order_id,)
            ).fetchone()
            return None if row is None else self._row_to_work_order(row)

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
