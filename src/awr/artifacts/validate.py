from __future__ import annotations

import json
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Never

from .contracts import (
    REASON_ACTIVE_CONTENT,
    REASON_MALFORMED,
    REASON_SCANNER_UNAVAILABLE,
    ArtifactStatus,
)
from .detect import DetectedFamily

yaml: Any = None
try:
    import yaml as _yaml
except ImportError:
    pass
else:
    yaml = _yaml

Image: Any = None
try:
    from PIL import Image as _Image
except ImportError:
    pass
else:
    Image = _Image

PdfReader: Any = None
try:
    from pypdf import PdfReader as _PdfReader
except ImportError:
    pass
else:
    PdfReader = _PdfReader

PILLOW_AVAILABLE = Image is not None
PYYAML_AVAILABLE = yaml is not None
PYPDF_AVAILABLE = PdfReader is not None

MAX_JSON_DEPTH = 32
MAX_JSON_COLLECTION = 10_000
MAX_YAML_NODES = 10_000
MAX_YAML_DEPTH = 32
MAX_YAML_COLLECTION = 10_000
MAX_IMAGE_WIDTH = 8192
MAX_IMAGE_HEIGHT = 8192
MAX_IMAGE_PIXELS = 4096 * 4096
MAX_IMAGE_METADATA = 64 * 1024
_ALLOWED_TEXT_CONTROLS = frozenset({9, 10, 13})
_PDF_NAME_DELIMITERS = frozenset(b" \t\r\n()<>[]{}/%")
_PDF_ACTIVE_NAMES = (
    b"JavaScript",
    b"JS",
    b"Launch",
    b"EmbeddedFiles",
    b"RichMedia",
    b"OpenAction",
    b"AA",
    b"Encrypt",
)


@dataclass(frozen=True, slots=True)
class ValidationResult:
    ok: bool
    status: ArtifactStatus
    reason_code: str | None
    detail: str | None = None


def _assert_never_family(value: Never) -> Never:
    raise ValueError(f"Unhandled detected family: {value!r}")


def _ok() -> ValidationResult:
    return ValidationResult(True, ArtifactStatus.CLEAN, None)


def _reject(status: ArtifactStatus, reason: str, detail: str | None = None) -> ValidationResult:
    return ValidationResult(False, status, reason, detail)


def _unavailable(detail: str) -> ValidationResult:
    return _reject(ArtifactStatus.REJECTED_SCANNER_UNAVAILABLE, REASON_SCANNER_UNAVAILABLE, detail)


def _malformed(detail: str) -> ValidationResult:
    return _reject(ArtifactStatus.REJECTED_MALFORMED, REASON_MALFORMED, detail)


def _active(detail: str) -> ValidationResult:
    return _reject(ArtifactStatus.REJECTED_ACTIVE_CONTENT, REASON_ACTIVE_CONTENT, detail)


def validate_payload(family: DetectedFamily, payload: bytes) -> ValidationResult:
    match family:
        case DetectedFamily.TEXT | DetectedFamily.MARKDOWN:
            return validate_text(payload)
        case DetectedFamily.JSON:
            return validate_json(payload)
        case DetectedFamily.YAML:
            return validate_yaml(payload)
        case DetectedFamily.PNG:
            return validate_image(payload, expected="PNG")
        case DetectedFamily.JPEG:
            return validate_image(payload, expected="JPEG")
        case DetectedFamily.PDF:
            return validate_pdf(payload)
        case (
            DetectedFamily.ZIP
            | DetectedFamily.ELF
            | DetectedFamily.PE
            | DetectedFamily.OLE
            | DetectedFamily.SVG
            | DetectedFamily.UNKNOWN
        ):
            return _malformed("disallowed_family_reached_validator")
        case _:
            return _assert_never_family(family)


def validate_text(payload: bytes) -> ValidationResult:
    if b"\x00" in payload:
        return _malformed("nul_byte")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return _malformed("utf8")
    for char in text:
        code = ord(char)
        if code < 32 and code not in _ALLOWED_TEXT_CONTROLS:
            return _malformed("control_character")
    return _ok()


def validate_json(payload: bytes) -> ValidationResult:
    text_result = validate_text(payload)
    if not text_result.ok:
        return text_result
    try:
        parsed = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except ValueError as exc:
        return _malformed(str(exc))
    try:
        _assert_bounded_tree(parsed, MAX_JSON_DEPTH, MAX_JSON_COLLECTION)
    except ValueError as exc:
        return _malformed(str(exc))
    return _ok()


def validate_yaml(payload: bytes) -> ValidationResult:
    if yaml is None:
        return _unavailable("pyyaml_missing")
    text_result = validate_text(payload)
    if not text_result.ok:
        return text_result

    class BoundedSafeLoader(yaml.SafeLoader):  # type: ignore[misc]
        def __init__(self, stream: Any) -> None:
            super().__init__(stream)
            self._awr_nodes = 0

        def compose_node(self, parent: Any, index: Any) -> Any:
            self._awr_nodes += 1
            if self._awr_nodes > MAX_YAML_NODES:
                raise yaml.YAMLError("YAML exceeds node limit")
            return super().compose_node(parent, index)

    try:
        parsed = yaml.load(payload.decode("utf-8"), Loader=BoundedSafeLoader)
    except Exception as exc:  # noqa: BLE001
        return _malformed(f"yaml:{exc}")
    try:
        _assert_bounded_tree(parsed, MAX_YAML_DEPTH, MAX_YAML_COLLECTION)
    except ValueError as exc:
        return _malformed(str(exc))
    return _ok()


def validate_image(payload: bytes, *, expected: str) -> ValidationResult:
    if Image is None:
        return _unavailable("pillow_missing")
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with Image.open(BytesIO(payload)) as first:
            if first.format != expected:
                return _malformed(f"image_format:{first.format}")
            width, height = first.size
            if width > MAX_IMAGE_WIDTH or height > MAX_IMAGE_HEIGHT:
                return _malformed("image_dimensions")
            if width * height > MAX_IMAGE_PIXELS:
                return _malformed("image_pixels")
            metadata_bytes = len(repr(getattr(first, "info", {})).encode("utf-8", errors="replace"))
            if metadata_bytes > MAX_IMAGE_METADATA:
                return _malformed("image_metadata")
            first.verify()
        with Image.open(BytesIO(payload)) as second:
            second.load()
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        if "DecompressionBomb" in name:
            return _malformed("image_pixels")
        return _malformed(f"image:{name}")
    return _ok()


def _has_pdf_name(payload: bytes, name: bytes) -> bool:
    token = b"/" + name
    start = 0
    while True:
        index = payload.find(token, start)
        if index < 0:
            return False
        end = index + len(token)
        if end == len(payload) or payload[end] in _PDF_NAME_DELIMITERS:
            return True
        start = index + 1


def validate_pdf(payload: bytes) -> ValidationResult:
    for name in _PDF_ACTIVE_NAMES:
        if _has_pdf_name(payload, name):
            if name == b"Encrypt":
                return _active("pdf_encrypted")
            return _active("pdf_active_content")
    if PdfReader is None:
        return _unavailable("pypdf_missing")
    try:
        reader = PdfReader(BytesIO(payload), strict=True)
    except Exception as exc:  # noqa: BLE001
        return _malformed(f"pdf:{type(exc).__name__}")
    if getattr(reader, "is_encrypted", False):
        return _active("pdf_encrypted")
    attachments = getattr(reader, "attachments", None)
    try:
        attached = len(attachments) if attachments is not None else 0
    except TypeError:
        attached = 0
    if attached:
        return _active("pdf_embedded_files")
    try:
        pages = reader.pages
        if pages is not None:
            len(pages)
    except Exception as exc:  # noqa: BLE001
        return _malformed(f"pdf_pages:{type(exc).__name__}")
    return _ok()


def _reject_duplicate_keys(pairs: list[tuple[Any, Any]]) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


def _assert_bounded_tree(value: Any, max_depth: int, max_collection: int, depth: int = 0) -> None:
    if depth > max_depth:
        raise ValueError("nesting_limit")
    if isinstance(value, dict):
        if len(value) > max_collection:
            raise ValueError("collection_limit")
        for item in value.values():
            _assert_bounded_tree(item, max_depth, max_collection, depth + 1)
        return
    if isinstance(value, list):
        if len(value) > max_collection:
            raise ValueError("collection_limit")
        for item in value:
            _assert_bounded_tree(item, max_depth, max_collection, depth + 1)
