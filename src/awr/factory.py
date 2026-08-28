from __future__ import annotations

import os
from pathlib import Path

from .executors.base import PlanningExecutor
from .executors.cursor_cloud import CursorCloudExecutor
from .executors.recording_cursor import RecordingCursorExecutor
from .service import BrokerService
from .storage.sqlite import SQLiteStateStore


def build_service(
    db_path: str | Path | None = None,
    executor: PlanningExecutor | None = None,
) -> BrokerService:
    storage = os.getenv("AWR_STORAGE", "sqlite")
    if storage != "sqlite":
        raise NotImplementedError(f"Storage adapter {storage!r} is not enabled in AWR-GT-001.")
    configured_path = (
        db_path if db_path is not None else os.getenv("AWR_SQLITE_PATH", ".awr/awr.db")
    )
    resolved_path = Path(configured_path)
    selected_executor = executor or _build_executor()
    return BrokerService(
        store=SQLiteStateStore(resolved_path),
        executor=selected_executor,
        default_repository_url=os.getenv("AWR_REPOSITORY_URL") or None,
        default_base_ref=os.getenv("AWR_BASE_REF", "main"),
    )


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
