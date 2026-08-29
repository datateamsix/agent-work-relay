from __future__ import annotations

import hashlib
import threading
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ..contracts import LedgerEntry, WorkAction, WorkKind, WorkOrder, WorkStatus
from .base import WorkOrderSession

WORK_ORDERS = "awr_work_orders"
IDEMPOTENCY = "awr_idempotency"
LEDGER = "ledger"


def _idempotency_id(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class FirestoreStateStore:
    """Firestore adapter for the StateStore port.

    Work-order documents are materialized snapshots. Ledger documents are
    append-only. Sequence numbers are allocated on the work-order document
    inside a transaction.
    """

    def __init__(self, client: Any, *, database: str = "(default)") -> None:
        self._client = client
        self.database = database
        self._locks: dict[str, threading.RLock] = defaultdict(threading.RLock)

    @classmethod
    def from_env(
        cls, *, project: str | None = None, database: str = "(default)"
    ) -> FirestoreStateStore:
        from google.cloud.firestore import Client

        return cls(Client(project=project, database=database), database=database)

    def _run_transaction(self, fn: Any) -> Any:
        runner = getattr(self._client, "run_transaction", None)
        if callable(runner):
            return runner(fn)
        from google.cloud import firestore

        transaction = self._client.transaction()

        @firestore.transactional
        def wrapped(transaction: Any) -> Any:
            return fn(transaction)

        return wrapped(transaction)

    def _work_order_ref(self, work_order_id: str) -> Any:
        return self._client.collection(WORK_ORDERS).document(work_order_id)

    def _idempotency_ref(self, idempotency_key: str) -> Any:
        return self._client.collection(IDEMPOTENCY).document(_idempotency_id(idempotency_key))

    def _ledger_collection(self, work_order_id: str) -> Any:
        return self._work_order_ref(work_order_id).collection(LEDGER)

    def create_work_order(self, work_order: WorkOrder) -> tuple[WorkOrder, bool, int]:
        def txn(transaction: Any) -> tuple[WorkOrder, bool, int]:
            idem_ref = self._idempotency_ref(work_order.idempotency_key)
            existing_claim = _snapshot(idem_ref, transaction)
            if existing_claim.exists:
                existing_id = str(existing_claim.get("work_order_id"))
                stored = self._read_work_order(existing_id, transaction)
                if stored is None:
                    raise KeyError(f"Idempotency claim is missing work order {existing_id}")
                sequence = int(
                    _snapshot(self._work_order_ref(existing_id), transaction).get("ledger_sequence")
                )
                return stored, False, sequence

            work_ref = self._work_order_ref(work_order.work_order_id)
            if _snapshot(work_ref, transaction).exists:
                raise ValueError(f"Work order already exists: {work_order.work_order_id}")

            payload = _work_order_to_doc(work_order)
            payload["ledger_sequence"] = 1
            transaction.set(work_ref, payload)
            transaction.set(
                idem_ref,
                {
                    "work_order_id": work_order.work_order_id,
                    "idempotency_key": work_order.idempotency_key,
                },
            )
            accepted_payload = {
                "content_sha256": work_order.content_sha256,
                "wrapper": f"{work_order.wrapper_id}@{work_order.wrapper_version}",
            }
            if work_order.bundle_sha256 is not None:
                accepted_payload["bundle_sha256"] = work_order.bundle_sha256
            entry = _new_ledger_entry(
                sequence=1,
                work_order_id=work_order.work_order_id,
                event_type="work_order.accepted",
                actor=work_order.sender,
                counterparty="broker:awr",
                payload=accepted_payload,
            )
            transaction.set(
                self._ledger_collection(work_order.work_order_id).document(entry.event_id),
                _ledger_to_doc(entry),
            )
            return work_order, True, 1

        result: tuple[WorkOrder, bool, int] = self._run_transaction(txn)
        return result

    def get_work_order(self, work_order_id: str) -> WorkOrder | None:
        snapshot = self._work_order_ref(work_order_id).get()
        if not snapshot.exists:
            return None
        return _doc_to_work_order(snapshot.to_dict())

    def get_by_idempotency_key(self, idempotency_key: str) -> WorkOrder | None:
        snapshot = self._idempotency_ref(idempotency_key).get()
        if not snapshot.exists:
            return None
        return self.get_work_order(str(snapshot.get("work_order_id")))

    def update_status(self, work_order_id: str, status: WorkStatus) -> None:
        def txn(transaction: Any) -> None:
            ref = self._work_order_ref(work_order_id)
            snapshot = _snapshot(ref, transaction)
            if not snapshot.exists:
                raise KeyError(f"Unknown work order: {work_order_id}")
            transaction.update(ref, {"status": status.value})

        self._run_transaction(txn)

    def append_ledger(
        self,
        work_order_id: str,
        event_type: str,
        actor: str,
        counterparty: str,
        payload: dict[str, Any],
    ) -> LedgerEntry:
        def txn(transaction: Any) -> LedgerEntry:
            return self._append_on_transaction(
                transaction, work_order_id, event_type, actor, counterparty, payload
            )

        result: LedgerEntry = self._run_transaction(txn)
        return result

    def _append_on_transaction(
        self,
        transaction: Any,
        work_order_id: str,
        event_type: str,
        actor: str,
        counterparty: str,
        payload: dict[str, Any],
    ) -> LedgerEntry:
        ref = self._work_order_ref(work_order_id)
        snapshot = _snapshot(ref, transaction)
        if not snapshot.exists:
            raise KeyError(f"Unknown work order: {work_order_id}")
        sequence = int(snapshot.get("ledger_sequence")) + 1
        entry = _new_ledger_entry(
            sequence=sequence,
            work_order_id=work_order_id,
            event_type=event_type,
            actor=actor,
            counterparty=counterparty,
            payload=payload,
        )
        transaction.update(ref, {"ledger_sequence": sequence})
        transaction.set(
            self._ledger_collection(work_order_id).document(entry.event_id), _ledger_to_doc(entry)
        )
        return entry

    def list_ledger(self, work_order_id: str | None = None) -> list[LedgerEntry]:
        if work_order_id is not None:
            return self._list_ledger(work_order_id)
        entries: list[LedgerEntry] = []
        for snapshot in self._client.collection(WORK_ORDERS).order_by("created_at").stream():
            data = snapshot.to_dict() or {}
            entries.extend(self._list_ledger(str(data["work_order_id"])))
        return entries

    def _list_ledger(self, work_order_id: str, transaction: Any | None = None) -> list[LedgerEntry]:
        query = self._ledger_collection(work_order_id).order_by("sequence")
        if transaction is None:
            snapshots = query.stream()
        else:
            snapshots = query.stream(transaction=transaction)
        return [_doc_to_ledger(snapshot.to_dict()) for snapshot in snapshots]

    def _read_work_order(self, work_order_id: str, transaction: Any) -> WorkOrder | None:
        snapshot = _snapshot(self._work_order_ref(work_order_id), transaction)
        if not snapshot.exists:
            return None
        return _doc_to_work_order(snapshot.to_dict())

    @contextmanager
    def lock_work_order(self, work_order_id: str) -> Iterator[WorkOrderSession]:
        with self._locks[work_order_id]:
            current = self.get_work_order(work_order_id)
            if current is None:
                raise KeyError(f"Unknown work order: {work_order_id}")
            session = _FirestoreWorkOrderSession(
                store=self,
                work_order=current,
                ledger=self._list_ledger(work_order_id),
            )
            yield session
            session.commit()


class _FirestoreWorkOrderSession:
    def __init__(
        self,
        store: FirestoreStateStore,
        work_order: WorkOrder,
        ledger: list[LedgerEntry],
    ) -> None:
        self._store = store
        self._work_order = work_order
        self._ledger = list(ledger)
        self._base_sequence = max((entry.sequence for entry in ledger), default=0)
        self._pending_status: WorkStatus | None = None
        self._pending_entries: list[LedgerEntry] = []
        self._pending_packets: list[dict[str, Any]] = []
        self._pending_decisions: list[dict[str, Any]] = []
        self._pending_lifecycle: dict[str, Any] | None = None
        self._lifecycle: dict[str, Any] | None = self._load_lifecycle()
        self._decisions: list[dict[str, Any]] = self._load_decisions()

    def _responses_collection(self) -> Any:
        return self._store._work_order_ref(self._work_order.work_order_id).collection("responses")

    def _decisions_collection(self) -> Any:
        return self._store._work_order_ref(self._work_order.work_order_id).collection("decisions")

    def _lifecycle_ref(self) -> Any:
        return (
            self._store._work_order_ref(self._work_order.work_order_id)
            .collection("lifecycle")
            .document("snapshot")
        )

    def _load_lifecycle(self) -> dict[str, Any] | None:
        snapshot = self._lifecycle_ref().get()
        if not snapshot.exists:
            return None
        data = snapshot.to_dict() or {}
        return data if isinstance(data, dict) else None

    def _load_decisions(self) -> list[dict[str, Any]]:
        return [item.to_dict() or {} for item in self._decisions_collection().stream()]

    def get_work_order(self) -> WorkOrder:
        return self._work_order

    def list_ledger(self) -> list[LedgerEntry]:
        return list(self._ledger)

    def update_status(self, status: WorkStatus) -> None:
        self._pending_status = status
        self._work_order = replace(self._work_order, status=status)

    def append_ledger(
        self,
        event_type: str,
        actor: str,
        counterparty: str,
        payload: dict[str, Any],
    ) -> LedgerEntry:
        sequence = self._base_sequence + len(self._pending_entries) + 1
        entry = _new_ledger_entry(
            sequence=sequence,
            work_order_id=self._work_order.work_order_id,
            event_type=event_type,
            actor=actor,
            counterparty=counterparty,
            payload=payload,
        )
        self._pending_entries.append(entry)
        self._ledger.append(entry)
        return entry

    def put_response_packet(self, packet: dict[str, Any]) -> None:
        self._pending_packets.append(packet)

    def get_response_by_idempotency(
        self, actor: str, response_type: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        for item in self._pending_packets:
            if (
                item.get("actor") == actor
                and item.get("response_type") == response_type
                and item.get("idempotency_key") == idempotency_key
            ):
                return {"packet": item["packet"], "content_sha256": item["content_sha256"]}
        for snapshot in self._responses_collection().stream():
            data = snapshot.to_dict() or {}
            if (
                data.get("actor") == actor
                and data.get("response_type") == response_type
                and data.get("idempotency_key") == idempotency_key
            ):
                packet = data.get("packet")
                if isinstance(packet, dict):
                    return {
                        "packet": packet,
                        "content_sha256": str(data.get("content_sha256") or ""),
                    }
        return None

    def put_decision(self, decision: dict[str, Any]) -> None:
        self._pending_decisions.append(decision)
        self._decisions.append(decision)

    def list_decisions(self) -> list[dict[str, Any]]:
        return list(self._decisions)

    def put_lifecycle(self, snapshot: dict[str, Any]) -> None:
        self._pending_lifecycle = snapshot
        self._lifecycle = snapshot

    def get_lifecycle(self) -> dict[str, Any] | None:
        return None if self._lifecycle is None else dict(self._lifecycle)

    def commit(self) -> None:
        if (
            self._pending_status is None
            and not self._pending_entries
            and not self._pending_packets
            and not self._pending_decisions
            and self._pending_lifecycle is None
        ):
            return

        def txn(transaction: Any) -> None:
            ref = self._store._work_order_ref(self._work_order.work_order_id)
            snapshot = _snapshot(ref, transaction)
            if not snapshot.exists:
                raise KeyError(f"Unknown work order: {self._work_order.work_order_id}")
            current_sequence = int(snapshot.get("ledger_sequence"))
            existing_types = {
                str(item.to_dict()["event_type"])
                for item in self._store._ledger_collection(self._work_order.work_order_id)
                .order_by("sequence")
                .stream(transaction=transaction)
            }
            if current_sequence != self._base_sequence:
                remaining = [
                    entry
                    for entry in self._pending_entries
                    if entry.event_type not in existing_types
                ]
                if not remaining and (
                    self._pending_status is None
                    or snapshot.get("status")
                    == (self._pending_status.value if self._pending_status else None)
                    or self._pending_status is WorkStatus.PLAN_READY
                    and snapshot.get("status") == WorkStatus.PLAN_READY.value
                ):
                    return
                raise RuntimeError(
                    f"Concurrent ledger update for {self._work_order.work_order_id}."
                )

            updates: dict[str, Any] = {
                "ledger_sequence": current_sequence + len(self._pending_entries)
            }
            if self._pending_status is not None:
                updates["status"] = self._pending_status.value
            transaction.update(ref, updates)
            for entry in self._pending_entries:
                transaction.set(
                    self._store._ledger_collection(self._work_order.work_order_id).document(
                        entry.event_id
                    ),
                    _ledger_to_doc(entry),
                )
            for packet in self._pending_packets:
                transaction.set(
                    self._responses_collection().document(str(packet["packet_id"])),
                    packet,
                )
            for decision in self._pending_decisions:
                transaction.set(
                    self._decisions_collection().document(str(decision["decision_id"])),
                    decision,
                )
            if self._pending_lifecycle is not None:
                transaction.set(self._lifecycle_ref(), self._pending_lifecycle)

        self._store._run_transaction(txn)


def _snapshot(ref: Any, transaction: Any) -> Any:
    getter = getattr(transaction, "get", None)
    if callable(getter):
        result = getter(ref)
        if not isinstance(result, list):
            return result
    return ref.get(transaction=transaction)


def _work_order_to_doc(work_order: WorkOrder) -> dict[str, Any]:
    return {
        "work_order_id": work_order.work_order_id,
        "idempotency_key": work_order.idempotency_key,
        "sender": work_order.sender,
        "recipient": work_order.recipient,
        "kind": work_order.kind.value,
        "action": work_order.action.value,
        "parent_work_order_id": work_order.parent_work_order_id,
        "repository_url": work_order.repository_url,
        "base_ref": work_order.base_ref,
        "markdown": work_order.markdown,
        "content_sha256": work_order.content_sha256,
        "wrapper_id": work_order.wrapper_id,
        "wrapper_version": work_order.wrapper_version,
        "wrapper_sha256": work_order.wrapper_sha256,
        "status": work_order.status.value,
        "created_at": work_order.created_at,
        "bundle_sha256": work_order.bundle_sha256,
    }


def _doc_to_work_order(data: dict[str, Any] | None) -> WorkOrder:
    if not data:
        raise KeyError("Work order document is empty.")
    return WorkOrder(
        work_order_id=str(data["work_order_id"]),
        idempotency_key=str(data["idempotency_key"]),
        sender=str(data["sender"]),
        recipient=str(data["recipient"]),
        kind=WorkKind(str(data["kind"])),
        action=WorkAction(str(data["action"])),
        parent_work_order_id=data.get("parent_work_order_id"),
        repository_url=str(data["repository_url"]),
        base_ref=str(data["base_ref"]),
        markdown=str(data["markdown"]),
        content_sha256=str(data["content_sha256"]),
        wrapper_id=str(data["wrapper_id"]),
        wrapper_version=str(data["wrapper_version"]),
        wrapper_sha256=str(data["wrapper_sha256"]),
        status=WorkStatus(str(data["status"])),
        created_at=str(data["created_at"]),
        bundle_sha256=str(data["bundle_sha256"]) if data.get("bundle_sha256") else None,
    )


def _new_ledger_entry(
    *,
    sequence: int,
    work_order_id: str,
    event_type: str,
    actor: str,
    counterparty: str,
    payload: dict[str, Any],
) -> LedgerEntry:
    return LedgerEntry(
        sequence=sequence,
        event_id=f"evt-{uuid4()}",
        work_order_id=work_order_id,
        event_type=event_type,
        actor=actor,
        counterparty=counterparty,
        payload=payload,
        created_at=datetime.now(UTC).isoformat(),
    )


def _ledger_to_doc(entry: LedgerEntry) -> dict[str, Any]:
    return {
        "sequence": entry.sequence,
        "event_id": entry.event_id,
        "work_order_id": entry.work_order_id,
        "event_type": entry.event_type,
        "actor": entry.actor,
        "counterparty": entry.counterparty,
        "payload": entry.payload,
        "created_at": entry.created_at,
    }


def _doc_to_ledger(data: dict[str, Any] | None) -> LedgerEntry:
    if not data:
        raise KeyError("Ledger document is empty.")
    payload = data.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    return LedgerEntry(
        sequence=int(data["sequence"]),
        event_id=str(data["event_id"]),
        work_order_id=str(data["work_order_id"]),
        event_type=str(data["event_type"]),
        actor=str(data["actor"]),
        counterparty=str(data["counterparty"]),
        payload=payload,
        created_at=str(data["created_at"]),
    )
