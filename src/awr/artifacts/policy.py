from __future__ import annotations

from pathlib import Path

from .contracts import CONTROL_AUTHORITY_PRIMARY_MARKDOWN
from .detect import DetectedFamily, DetectionResult, family_from_filename, family_from_media_type

ALLOWED_FAMILIES = frozenset(
    {
        DetectedFamily.TEXT,
        DetectedFamily.MARKDOWN,
        DetectedFamily.JSON,
        DetectedFamily.YAML,
        DetectedFamily.PNG,
        DetectedFamily.JPEG,
        DetectedFamily.PDF,
    }
)

AWR_DIRECTIVE_MARK = b"@awr"
INPUT_DIRECTIVE_MARK = b"@input"


def contains_awr_directive_text(payload: bytes) -> bool:
    return AWR_DIRECTIVE_MARK in payload or INPUT_DIRECTIVE_MARK in payload


def control_authority_diagnostics(payload: bytes) -> dict[str, object]:
    return {
        "control_authority": CONTROL_AUTHORITY_PRIMARY_MARKDOWN,
        "contains_awr_directive_text": contains_awr_directive_text(payload),
    }


def type_conflict_reason(
    *,
    declared_media_type: str,
    original_filename: str,
    detection: DetectionResult,
) -> str | None:
    declared_family = family_from_media_type(declared_media_type)
    extension_family = family_from_filename(Path(original_filename).name)
    if detection.polyglot:
        return "polyglot"
    if detection.family not in ALLOWED_FAMILIES:
        return "disallowed"
    if declared_family is not None and declared_family is not detection.family:
        return "declared"
    if extension_family is not None and extension_family is not detection.family:
        return "extension"
    if (
        declared_family is not None
        and extension_family is not None
        and declared_family is not extension_family
    ):
        return "declared"
    return None
