from __future__ import annotations

import os
from pathlib import Path

from .artifacts.service import ArtifactService
from .executors.base import PlanningExecutor
from .executors.cursor_cloud import CursorCloudExecutor
from .executors.recording_cursor import RecordingCursorExecutor
from .service import BrokerService
from .storage.artifact_fs import LocalArtifactBodyStore
from .storage.artifact_sqlite import SQLiteArtifactMetadataStore
from .storage.base import StateStore
from .storage.firestore import FirestoreStateStore
from .storage.firestore_memory import InMemoryFirestore
from .storage.sqlite import SQLiteStateStore


def build_service(
    db_path: str | Path | None = None,
    executor: PlanningExecutor | None = None,
    store: StateStore | None = None,
) -> BrokerService:
    selected_store = store or _build_store(db_path)
    selected_executor = executor or _build_executor()
    return BrokerService(
        store=selected_store,
        executor=selected_executor,
        default_repository_url=os.getenv("AWR_REPOSITORY_URL") or None,
        default_base_ref=os.getenv("AWR_BASE_REF", "main"),
    )


def build_artifact_service(
    db_path: str | Path | None = None,
    artifact_root: str | Path | None = None,
    max_bytes: int | None = None,
) -> ArtifactService:
    sqlite_path = Path(
        db_path if db_path is not None else os.getenv("AWR_SQLITE_PATH", ".awr/awr.db")
    )
    root = Path(
        artifact_root
        if artifact_root is not None
        else os.getenv("AWR_ARTIFACT_ROOT", ".awr/artifacts")
    )
    limit = max_bytes
    if limit is None:
        raw = os.getenv("AWR_ARTIFACT_MAX_BYTES")
        limit = int(raw) if raw else 10 * 1024 * 1024
    return ArtifactService(
        SQLiteArtifactMetadataStore(sqlite_path),
        LocalArtifactBodyStore(root),
        max_bytes=limit,
    )


def _build_store(db_path: str | Path | None) -> StateStore:
    storage = os.getenv("AWR_STORAGE", "sqlite")
    if storage == "sqlite":
        configured_path = (
            db_path if db_path is not None else os.getenv("AWR_SQLITE_PATH", ".awr/awr.db")
        )
        return SQLiteStateStore(Path(configured_path))
    if storage == "memory_firestore":
        return FirestoreStateStore(InMemoryFirestore())
    if storage == "firestore":
        return FirestoreStateStore.from_env(
            project=os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("AWR_GCP_PROJECT"),
            database=os.getenv("FIRESTORE_DATABASE", "(default)"),
        )
    raise NotImplementedError(f"Storage adapter {storage!r} is not enabled.")


def _build_executor() -> PlanningExecutor:
    selected = os.getenv("AWR_EXECUTOR", "recording_cursor")
    if selected == "recording_cursor":
        return RecordingCursorExecutor()
    if selected == "cursor_cloud":
        api_key = os.getenv("CURSOR_API_KEY", "")
        if not api_key:
            raise RuntimeError("CURSOR_API_KEY is required when AWR_EXECUTOR=cursor_cloud.")
        return CursorCloudExecutor(
            api_key=api_key,
            base_url=os.getenv("CURSOR_API_BASE_URL", "https://api.cursor.com"),
        )
    raise NotImplementedError(f"Executor adapter {selected!r} is not configured.")
