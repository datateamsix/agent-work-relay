from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Any


class DocumentSnapshot:
    def __init__(self, path: tuple[str, ...], data: dict[str, Any] | None) -> None:
        self._path = path
        self._data = None if data is None else dict(data)

    @property
    def exists(self) -> bool:
        return self._data is not None

    @property
    def id(self) -> str:
        return self._path[-1] if self._path else ""

    def to_dict(self) -> dict[str, Any] | None:
        return None if self._data is None else dict(self._data)

    def get(self, field: str) -> Any:
        if self._data is None:
            raise KeyError(field)
        return self._data[field]


class DocumentReference:
    def __init__(self, client: InMemoryFirestore, path: tuple[str, ...]) -> None:
        self._client = client
        self._path = path

    @property
    def id(self) -> str:
        return self._path[-1]

    def collection(self, name: str) -> CollectionReference:
        return CollectionReference(self._client, self._path + (name,))

    def get(self, transaction: InMemoryTransaction | None = None) -> DocumentSnapshot:
        if transaction is not None:
            return transaction.get(self)
        with self._client._lock:
            return DocumentSnapshot(self._path, self._client._docs.get(self._path))

    def set(self, data: dict[str, Any], transaction: InMemoryTransaction | None = None) -> None:
        if transaction is not None:
            transaction.set(self, data)
            return
        with self._client._lock:
            self._client._docs[self._path] = dict(data)

    def update(self, data: dict[str, Any], transaction: InMemoryTransaction | None = None) -> None:
        if transaction is not None:
            transaction.update(self, data)
            return
        with self._client._lock:
            current = self._client._docs.get(self._path)
            if current is None:
                raise KeyError(self._path)
            current.update(data)

    def __hash__(self) -> int:
        return hash(self._path)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, DocumentReference) and other._path == self._path


class Query:
    def __init__(self, collection: CollectionReference, field: str) -> None:
        self._collection = collection
        self._field = field

    def stream(self, transaction: InMemoryTransaction | None = None) -> Iterator[DocumentSnapshot]:
        prefix = self._collection._path
        source = transaction._overlay if transaction is not None else None
        with self._collection._client._lock:
            items: list[DocumentSnapshot] = []
            paths = set(self._collection._client._docs)
            if source is not None:
                paths.update(source)
            for path in paths:
                if len(path) != len(prefix) + 1 or path[: len(prefix)] != prefix:
                    continue
                data = None
                if source is not None and path in source:
                    data = source[path]
                else:
                    data = self._collection._client._docs.get(path)
                if data is None:
                    continue
                items.append(DocumentSnapshot(path, data))
            items.sort(key=lambda snap: snap.to_dict()[self._field])  # type: ignore[index]
            return iter(items)


class CollectionReference:
    def __init__(self, client: InMemoryFirestore, path: tuple[str, ...]) -> None:
        self._client = client
        self._path = path

    def document(self, document_id: str) -> DocumentReference:
        return DocumentReference(self._client, self._path + (document_id,))

    def order_by(self, field: str) -> Query:
        return Query(self, field)

    def stream(self, transaction: InMemoryTransaction | None = None) -> Iterator[DocumentSnapshot]:
        prefix = self._path
        source = transaction._overlay if transaction is not None else None
        with self._client._lock:
            items: list[DocumentSnapshot] = []
            paths = set(self._client._docs)
            if source is not None:
                paths.update(source)
            for path in paths:
                if len(path) != len(prefix) + 1 or path[: len(prefix)] != prefix:
                    continue
                data = None
                if source is not None and path in source:
                    data = source[path]
                else:
                    data = self._client._docs.get(path)
                if data is None:
                    continue
                items.append(DocumentSnapshot(path, data))
            items.sort(key=lambda snap: snap.id)
            return iter(items)


class InMemoryTransaction:
    def __init__(self, client: InMemoryFirestore) -> None:
        self._client = client
        self._overlay: dict[tuple[str, ...], dict[str, Any] | None] = {}

    def get(self, ref: DocumentReference) -> DocumentSnapshot:
        if ref._path in self._overlay:
            return DocumentSnapshot(ref._path, self._overlay[ref._path])
        return DocumentSnapshot(ref._path, self._client._docs.get(ref._path))

    def set(self, ref: DocumentReference, data: dict[str, Any]) -> None:
        self._overlay[ref._path] = dict(data)

    def update(self, ref: DocumentReference, data: dict[str, Any]) -> None:
        current = self._overlay.get(ref._path)
        if current is None:
            stored = self._client._docs.get(ref._path)
            if stored is None:
                raise KeyError(ref._path)
            current = dict(stored)
        current.update(data)
        self._overlay[ref._path] = current

    def commit(self) -> None:
        for path, data in self._overlay.items():
            if data is None:
                self._client._docs.pop(path, None)
            else:
                self._client._docs[path] = dict(data)


class InMemoryFirestore:
    """Deterministic Firestore test double with document and transaction semantics."""

    def __init__(self) -> None:
        self._docs: dict[tuple[str, ...], dict[str, Any]] = {}
        self._lock = threading.RLock()

    def collection(self, name: str) -> CollectionReference:
        return CollectionReference(self, (name,))

    def transaction(self) -> InMemoryTransaction:
        return InMemoryTransaction(self)

    def run_transaction(self, fn: Any) -> Any:
        with self._lock:
            transaction = InMemoryTransaction(self)
            result = fn(transaction)
            transaction.commit()
            return result
