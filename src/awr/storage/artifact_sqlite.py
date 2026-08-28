from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from ..artifacts.contracts import (
    Artifact,
    ArtifactPurpose,
    ArtifactReceipt,
    ArtifactSecurityReceipt,
    ArtifactSecurityVerdict,
    ArtifactStatus,
    ScanClaim,
    allowed_transitions,
    is_orchestrator_status,
    is_rejection,
)
from ..artifacts.errors import ArtifactError
from .sqlite import SQLiteStateStore


class SQLiteArtifactMetadataStore:
    """SQLite metadata, security receipts, and artifact receipt journal."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        SQLiteStateStore(self.path)

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

    def declare(self, artifact: Artifact) -> tuple[Artifact, bool]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM artifacts
                WHERE owner = ? AND idempotency_key = ?
                """,
                (artifact.owner, artifact.idempotency_key),
            ).fetchone()
            if existing is not None:
                loaded = self._row_to_artifact(existing)
                self._validate_replay(artifact, loaded)
                return loaded, False
            connection.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, idempotency_key, owner, original_filename,
                    declared_media_type, detected_media_type, byte_length, sha256,
                    purpose, status, parent_artifact_id, correlation_id,
                    expected_byte_length, expected_sha256,
                    scan_lease_id, scan_lease_expires_at, scan_attempt, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.artifact_id,
                    artifact.idempotency_key,
                    artifact.owner,
                    artifact.original_filename,
                    artifact.declared_media_type,
                    artifact.detected_media_type,
                    artifact.byte_length,
                    artifact.sha256,
                    artifact.purpose.value,
                    artifact.status.value,
                    artifact.parent_artifact_id,
                    artifact.correlation_id,
                    artifact.expected_byte_length,
                    artifact.expected_sha256,
                    artifact.scan_lease_id,
                    artifact.scan_lease_expires_at,
                    artifact.scan_attempt,
                    artifact.created_at,
                ),
            )
            self._append_receipt_on_connection(
                connection,
                artifact_id=artifact.artifact_id,
                event_type="artifact.declared",
                actor=artifact.owner,
                counterparty="broker:awr",
                correlation_id=artifact.correlation_id,
                payload={
                    "purpose": artifact.purpose.value,
                    "declared_media_type": artifact.declared_media_type,
                    "original_filename": artifact.original_filename,
                    "expected_byte_length": artifact.expected_byte_length,
                    "expected_sha256": artifact.expected_sha256,
                },
            )
            return artifact, True

    def get(self, artifact_id: str) -> Artifact | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            return None if row is None else self._row_to_artifact(row)

    def get_by_idempotency_key(self, owner: str, idempotency_key: str) -> Artifact | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE owner = ? AND idempotency_key = ?",
                (owner, idempotency_key),
            ).fetchone()
            return None if row is None else self._row_to_artifact(row)

    def update_status(self, artifact_id: str, status: ArtifactStatus) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._update_status_on_connection(connection, artifact_id, status)

    def put_security_receipt(self, receipt: ArtifactSecurityReceipt) -> ArtifactSecurityReceipt:
        with self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO artifact_security_receipts (
                        receipt_id, artifact_id, scanner_id, scanner_version,
                        signature_version, verdict, reason_codes_json, scanned_sha256,
                        started_at, completed_at, diagnostics_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        receipt.receipt_id,
                        receipt.artifact_id,
                        receipt.scanner_id,
                        receipt.scanner_version,
                        receipt.signature_version,
                        receipt.verdict.value,
                        json.dumps(list(receipt.reason_codes), separators=(",", ":")),
                        receipt.scanned_sha256,
                        receipt.started_at,
                        receipt.completed_at,
                        json.dumps(receipt.diagnostics, sort_keys=True, separators=(",", ":")),
                    ),
                )
            except sqlite3.IntegrityError:
                existing = self._security_receipt_on_connection(
                    connection, receipt.artifact_id, receipt.scanned_sha256
                )
                if existing is not None:
                    return existing
                raise
            return receipt

    def list_security_receipts(self, artifact_id: str) -> list[ArtifactSecurityReceipt]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM artifact_security_receipts
                WHERE artifact_id = ? ORDER BY completed_at
                """,
                (artifact_id,),
            ).fetchall()
            return [self._row_to_security_receipt(row) for row in rows]

    def get_security_receipt_for_digest(
        self, artifact_id: str, sha256: str
    ) -> ArtifactSecurityReceipt | None:
        with self._connection() as connection:
            return self._security_receipt_on_connection(connection, artifact_id, sha256)

    def list_artifacts(self) -> list[Artifact]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM artifacts ORDER BY created_at").fetchall()
            return [self._row_to_artifact(row) for row in rows]

    def claim_scan_lease(
        self,
        artifact_id: str,
        *,
        now: datetime,
        lease_ttl_seconds: float,
        lease_id: str,
    ) -> ScanClaim | None:
        expires_at = (now + timedelta(seconds=lease_ttl_seconds)).isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown artifact: {artifact_id}")
            artifact = self._row_to_artifact(row)
            if artifact.status is ArtifactStatus.CLEAN or is_rejection(artifact.status):
                return None
            if artifact.status in {ArtifactStatus.READY_TO_RELAY, ArtifactStatus.RELAYED}:
                return None
            if not artifact.sha256:
                raise ArtifactError("Cannot scan an artifact without a quarantined digest.")
            receipt = self._security_receipt_on_connection(connection, artifact_id, artifact.sha256)
            if receipt is not None:
                if artifact.status is ArtifactStatus.SCANNING:
                    connection.execute(
                        """
                        UPDATE artifacts
                        SET scan_lease_id = ?, scan_lease_expires_at = ?
                        WHERE artifact_id = ?
                        """,
                        (lease_id, expires_at, artifact_id),
                    )
                elif ArtifactStatus.SCANNING in allowed_transitions(artifact.status):
                    connection.execute(
                        """
                        UPDATE artifacts
                        SET status = ?, scan_lease_id = ?, scan_lease_expires_at = ?
                        WHERE artifact_id = ?
                        """,
                        (ArtifactStatus.SCANNING.value, lease_id, expires_at, artifact_id),
                    )
                else:
                    return None
                return ScanClaim(
                    artifact_id=artifact_id,
                    generation_sha256=artifact.sha256,
                    lease_id=lease_id,
                    expires_at=expires_at,
                    scan_attempt=artifact.scan_attempt,
                    already_scanned=True,
                    existing_receipt=receipt,
                )
            if artifact.status is ArtifactStatus.SCANNING and not _lease_expired(artifact, now):
                return None
            if (
                artifact.status is not ArtifactStatus.QUARANTINED
                and artifact.status is not ArtifactStatus.SCANNING
            ):
                return None
            next_attempt = artifact.scan_attempt + 1
            connection.execute(
                """
                UPDATE artifacts
                SET status = ?, scan_lease_id = ?, scan_lease_expires_at = ?, scan_attempt = ?
                WHERE artifact_id = ?
                """,
                (
                    ArtifactStatus.SCANNING.value,
                    lease_id,
                    expires_at,
                    next_attempt,
                    artifact_id,
                ),
            )
            self._append_receipt_on_connection(
                connection,
                artifact_id=artifact_id,
                event_type="artifact.scan_started",
                actor="broker:awr",
                counterparty="broker:awr",
                payload={"sha256": artifact.sha256, "scan_attempt": next_attempt},
                correlation_id=artifact.correlation_id,
            )
            return ScanClaim(
                artifact_id=artifact_id,
                generation_sha256=artifact.sha256,
                lease_id=lease_id,
                expires_at=expires_at,
                scan_attempt=next_attempt,
                already_scanned=False,
                existing_receipt=None,
            )

    def complete_scan(
        self,
        artifact_id: str,
        *,
        lease_id: str,
        status: ArtifactStatus,
        detected_media_type: str | None,
        now: datetime,
    ) -> Artifact:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown artifact: {artifact_id}")
            artifact = self._row_to_artifact(row)
            if artifact.status is status:
                return artifact
            if artifact.status is ArtifactStatus.CLEAN and status is ArtifactStatus.CLEAN:
                return artifact
            if is_rejection(artifact.status) and artifact.status is status:
                return artifact
            if artifact.status is not ArtifactStatus.SCANNING:
                raise ArtifactError(f"Cannot complete scan from status {artifact.status.value}.")
            digest = artifact.sha256 or ""
            receipt = self._security_receipt_on_connection(connection, artifact_id, digest)
            lease_ok = (
                artifact.scan_lease_id == lease_id
                or not artifact.scan_lease_id
                or _lease_expired(artifact, now)
                or receipt is not None
            )
            if not lease_ok:
                raise ArtifactError("Scan lease does not match this worker.")
            connection.execute(
                """
                UPDATE artifacts
                SET status = ?,
                    detected_media_type = COALESCE(?, detected_media_type),
                    scan_lease_id = NULL,
                    scan_lease_expires_at = NULL
                WHERE artifact_id = ?
                """,
                (status.value, detected_media_type, artifact_id),
            )
            if status is ArtifactStatus.CLEAN:
                self._append_receipt_on_connection(
                    connection,
                    artifact_id=artifact_id,
                    event_type="artifact.scan_passed",
                    actor="broker:awr",
                    counterparty="broker:awr",
                    payload={
                        "sha256": digest,
                        "detected_media_type": detected_media_type,
                    },
                    correlation_id=artifact.correlation_id,
                )
                self._append_receipt_on_connection(
                    connection,
                    artifact_id=artifact_id,
                    event_type="artifact.promoted",
                    actor="broker:awr",
                    counterparty="broker:awr",
                    payload={"sha256": digest},
                    correlation_id=artifact.correlation_id,
                )
            elif is_rejection(status):
                self._append_receipt_on_connection(
                    connection,
                    artifact_id=artifact_id,
                    event_type="artifact.rejected",
                    actor="broker:awr",
                    counterparty="broker:awr",
                    payload={"reason": status.value, "sha256": digest},
                    correlation_id=artifact.correlation_id,
                )
            updated = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            assert updated is not None
            return self._row_to_artifact(updated)

    def append_receipt(
        self,
        artifact_id: str,
        event_type: str,
        actor: str,
        counterparty: str,
        payload: dict[str, object],
        *,
        correlation_id: str,
        work_order_id: str | None = None,
    ) -> ArtifactReceipt:
        with self._connection() as connection:
            return self._append_receipt_on_connection(
                connection,
                artifact_id=artifact_id,
                event_type=event_type,
                actor=actor,
                counterparty=counterparty,
                payload=payload,
                correlation_id=correlation_id,
                work_order_id=work_order_id,
            )

    def list_receipts(self, artifact_id: str) -> list[ArtifactReceipt]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM artifact_receipts
                WHERE artifact_id = ? ORDER BY sequence
                """,
                (artifact_id,),
            ).fetchall()
            return [self._row_to_receipt(row) for row in rows]

    @contextmanager
    def lock_artifact(self, artifact_id: str) -> Iterator[_SQLiteArtifactSession]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown artifact: {artifact_id}")
            yield _SQLiteArtifactSession(self, connection, artifact_id)

    def _update_status_on_connection(
        self,
        connection: sqlite3.Connection,
        artifact_id: str,
        status: ArtifactStatus,
    ) -> None:
        row = connection.execute(
            "SELECT status FROM artifacts WHERE artifact_id = ?",
            (artifact_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown artifact: {artifact_id}")
        current = ArtifactStatus(row["status"])
        if is_orchestrator_status(status):
            raise ArtifactError(
                "SCANNING and CLEAN can only be set by the artifact security orchestrator."
            )
        if status is current:
            return
        if status not in allowed_transitions(current):
            raise ArtifactError(f"Invalid artifact transition {current.value} -> {status.value}.")
        cursor = connection.execute(
            "UPDATE artifacts SET status = ? WHERE artifact_id = ?",
            (status.value, artifact_id),
        )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown artifact: {artifact_id}")

    def _append_receipt_on_connection(
        self,
        connection: sqlite3.Connection,
        *,
        artifact_id: str,
        event_type: str,
        actor: str,
        counterparty: str,
        payload: dict[str, object],
        correlation_id: str,
        work_order_id: str | None = None,
    ) -> ArtifactReceipt:
        event_id = f"evt-{uuid4()}"
        try:
            cursor = connection.execute(
                """
                INSERT INTO artifact_receipts (
                    event_id, artifact_id, work_order_id, correlation_id, event_type,
                    actor, counterparty, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    artifact_id,
                    work_order_id,
                    correlation_id,
                    event_type,
                    actor,
                    counterparty,
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                ),
            )
        except sqlite3.IntegrityError:
            existing = connection.execute(
                """
                SELECT * FROM artifact_receipts
                WHERE artifact_id = ? AND event_type = ?
                ORDER BY sequence
                """,
                (artifact_id, event_type),
            ).fetchone()
            if existing is not None:
                return self._row_to_receipt(existing)
            raise
        row = connection.execute(
            "SELECT * FROM artifact_receipts WHERE sequence = ?",
            (cursor.lastrowid,),
        ).fetchone()
        assert row is not None
        return self._row_to_receipt(row)

    @staticmethod
    def _validate_replay(candidate: Artifact, existing: Artifact) -> None:
        comparable_candidate = (
            candidate.owner,
            candidate.original_filename,
            candidate.declared_media_type,
            candidate.purpose,
            candidate.parent_artifact_id,
            candidate.expected_byte_length,
            candidate.expected_sha256,
        )
        comparable_existing = (
            existing.owner,
            existing.original_filename,
            existing.declared_media_type,
            existing.purpose,
            existing.parent_artifact_id,
            existing.expected_byte_length,
            existing.expected_sha256,
        )
        if comparable_candidate != comparable_existing:
            raise ArtifactError(
                "The idempotency key is already bound to a different artifact payload."
            )

    @staticmethod
    def _row_to_artifact(row: sqlite3.Row) -> Artifact:
        return Artifact(
            artifact_id=row["artifact_id"],
            idempotency_key=row["idempotency_key"],
            owner=row["owner"],
            original_filename=row["original_filename"],
            declared_media_type=row["declared_media_type"],
            detected_media_type=row["detected_media_type"],
            byte_length=row["byte_length"],
            sha256=row["sha256"],
            purpose=ArtifactPurpose(row["purpose"]),
            status=ArtifactStatus(row["status"]),
            created_at=row["created_at"],
            parent_artifact_id=row["parent_artifact_id"],
            correlation_id=row["correlation_id"],
            expected_byte_length=row["expected_byte_length"],
            expected_sha256=row["expected_sha256"],
            scan_lease_id=_optional_str(row, "scan_lease_id"),
            scan_lease_expires_at=_optional_str(row, "scan_lease_expires_at"),
            scan_attempt=_optional_int(row, "scan_attempt"),
        )

    @staticmethod
    def _row_to_receipt(row: sqlite3.Row) -> ArtifactReceipt:
        return ArtifactReceipt(
            sequence=int(row["sequence"]),
            event_id=row["event_id"],
            artifact_id=row["artifact_id"],
            work_order_id=row["work_order_id"],
            correlation_id=row["correlation_id"],
            event_type=row["event_type"],
            actor=row["actor"],
            counterparty=row["counterparty"],
            payload=json.loads(row["payload_json"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_security_receipt(row: sqlite3.Row) -> ArtifactSecurityReceipt:
        reason_codes = json.loads(row["reason_codes_json"])
        diagnostics = json.loads(row["diagnostics_json"])
        return ArtifactSecurityReceipt(
            receipt_id=row["receipt_id"],
            artifact_id=row["artifact_id"],
            scanner_id=row["scanner_id"],
            scanner_version=row["scanner_version"],
            signature_version=row["signature_version"],
            verdict=ArtifactSecurityVerdict(row["verdict"]),
            reason_codes=tuple(str(code) for code in reason_codes),
            scanned_sha256=row["scanned_sha256"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            diagnostics=diagnostics if isinstance(diagnostics, dict) else {},
        )

    def _security_receipt_on_connection(
        self,
        connection: sqlite3.Connection,
        artifact_id: str,
        sha256: str,
    ) -> ArtifactSecurityReceipt | None:
        row = connection.execute(
            """
            SELECT * FROM artifact_security_receipts
            WHERE artifact_id = ? AND scanned_sha256 = ?
            """,
            (artifact_id, sha256),
        ).fetchone()
        return None if row is None else self._row_to_security_receipt(row)


def _optional_str(row: sqlite3.Row, name: str) -> str | None:
    try:
        value = row[name]
    except (IndexError, KeyError):
        return None
    if value is None:
        return None
    return str(value)


def _optional_int(row: sqlite3.Row, name: str) -> int:
    try:
        value = row[name]
    except (IndexError, KeyError):
        return 0
    if value is None:
        return 0
    return int(value)


def _lease_expired(artifact: Artifact, now: datetime) -> bool:
    if not artifact.scan_lease_id or not artifact.scan_lease_expires_at:
        return True
    parsed = datetime.fromisoformat(artifact.scan_lease_expires_at)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed <= now


class _SQLiteArtifactSession:
    def __init__(
        self,
        store: SQLiteArtifactMetadataStore,
        connection: sqlite3.Connection,
        artifact_id: str,
    ) -> None:
        self._store = store
        self._connection = connection
        self._artifact_id = artifact_id

    def get_artifact(self) -> Artifact:
        row = self._connection.execute(
            "SELECT * FROM artifacts WHERE artifact_id = ?",
            (self._artifact_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown artifact: {self._artifact_id}")
        return SQLiteArtifactMetadataStore._row_to_artifact(row)

    def update_status(self, status: ArtifactStatus) -> None:
        self._store._update_status_on_connection(self._connection, self._artifact_id, status)

    def set_fingerprint(self, *, sha256: str, byte_length: int) -> None:
        cursor = self._connection.execute(
            "UPDATE artifacts SET sha256 = ?, byte_length = ? WHERE artifact_id = ?",
            (sha256, byte_length, self._artifact_id),
        )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown artifact: {self._artifact_id}")

    def append_receipt(
        self,
        event_type: str,
        actor: str,
        counterparty: str,
        payload: dict[str, object],
    ) -> ArtifactReceipt:
        current = self.get_artifact()
        return self._store._append_receipt_on_connection(
            self._connection,
            artifact_id=self._artifact_id,
            event_type=event_type,
            actor=actor,
            counterparty=counterparty,
            payload=payload,
            correlation_id=current.correlation_id,
        )

    def list_receipts(self) -> list[ArtifactReceipt]:
        rows = self._connection.execute(
            """
            SELECT * FROM artifact_receipts
            WHERE artifact_id = ? ORDER BY sequence
            """,
            (self._artifact_id,),
        ).fetchall()
        return [SQLiteArtifactMetadataStore._row_to_receipt(row) for row in rows]
