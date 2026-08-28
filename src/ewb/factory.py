from __future__ import annotations

import os
from pathlib import Path

from .executors.base import PlanningExecutor
from .executors.recording_cursor import RecordingCursorExecutor
from .service import BrokerService
from .storage.sqlite import SQLiteStateStore


def build_service(
    db_path: str | Path | None = None,
    executor: PlanningExecutor | None = None,
) -> BrokerService:
    storage = os.getenv("EWB_STORAGE", "sqlite")
    if storage != "sqlite":
        raise NotImplementedError(f"Storage adapter {storage!r} is not enabled in EWB-GT-001.")
    configured_path = (
        db_path if db_path is not None else os.getenv("EWB_SQLITE_PATH", ".ewb/ewb.db")
    )
    resolved_path = Path(configured_path)
    return BrokerService(
        store=SQLiteStateStore(resolved_path),
        executor=executor or RecordingCursorExecutor(),
    )
