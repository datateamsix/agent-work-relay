from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Never

_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"
_PDF = b"%PDF-"
_ZIP = b"PK\x03\x04"
_ZIP_EMPTY = b"PK\x05\x06"
_ZIP_SPAN = b"PK\x07\x08"
_ELF = b"\x7fELF"
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_PE = b"MZ"

_TEXT_FAMILIES = frozenset({"text", "markdown", "json", "yaml"})


class DetectedFamily(StrEnum):
    PNG = "png"
    JPEG = "jpeg"
    PDF = "pdf"
    JSON = "json"
    YAML = "yaml"
    MARKDOWN = "markdown"
    TEXT = "text"
    ZIP = "zip"
    ELF = "elf"
    PE = "pe"
    OLE = "ole"
    SVG = "svg"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DetectionResult:
    family: DetectedFamily
    media_type: str
    polyglot: bool
    magics: tuple[str, ...]


_MEDIA_TYPES: dict[DetectedFamily, str] = {
    DetectedFamily.PNG: "image/png",
    DetectedFamily.JPEG: "image/jpeg",
    DetectedFamily.PDF: "application/pdf",
    DetectedFamily.JSON: "application/json",
    DetectedFamily.YAML: "application/yaml",
    DetectedFamily.MARKDOWN: "text/markdown",
    DetectedFamily.TEXT: "text/plain",
    DetectedFamily.ZIP: "application/zip",
    DetectedFamily.ELF: "application/x-elf",
    DetectedFamily.PE: "application/vnd.microsoft.portable-executable",
    DetectedFamily.OLE: "application/x-ole-storage",
    DetectedFamily.SVG: "image/svg+xml",
    DetectedFamily.UNKNOWN: "application/octet-stream",
}

_DECLARED_FAMILIES: dict[str, DetectedFamily] = {
    "text/plain": DetectedFamily.TEXT,
    "text/markdown": DetectedFamily.MARKDOWN,
    "text/x-markdown": DetectedFamily.MARKDOWN,
    "application/json": DetectedFamily.JSON,
    "text/json": DetectedFamily.JSON,
    "application/yaml": DetectedFamily.YAML,
    "text/yaml": DetectedFamily.YAML,
    "application/x-yaml": DetectedFamily.YAML,
    "text/x-yaml": DetectedFamily.YAML,
    "image/png": DetectedFamily.PNG,
    "image/jpeg": DetectedFamily.JPEG,
    "image/jpg": DetectedFamily.JPEG,
    "application/pdf": DetectedFamily.PDF,
    "application/zip": DetectedFamily.ZIP,
    "application/x-zip-compressed": DetectedFamily.ZIP,
    "image/svg+xml": DetectedFamily.SVG,
}

_EXTENSION_FAMILIES: dict[str, DetectedFamily] = {
    ".txt": DetectedFamily.TEXT,
    ".md": DetectedFamily.MARKDOWN,
    ".markdown": DetectedFamily.MARKDOWN,
    ".json": DetectedFamily.JSON,
    ".yaml": DetectedFamily.YAML,
    ".yml": DetectedFamily.YAML,
    ".png": DetectedFamily.PNG,
    ".jpg": DetectedFamily.JPEG,
    ".jpeg": DetectedFamily.JPEG,
    ".pdf": DetectedFamily.PDF,
    ".zip": DetectedFamily.ZIP,
    ".svg": DetectedFamily.SVG,
    ".exe": DetectedFamily.PE,
    ".dll": DetectedFamily.PE,
    ".elf": DetectedFamily.ELF,
    ".so": DetectedFamily.ELF,
    ".doc": DetectedFamily.OLE,
    ".xls": DetectedFamily.OLE,
    ".ppt": DetectedFamily.OLE,
}


def _assert_never_family(value: Never) -> Never:
    raise ValueError(f"Unhandled detected family: {value!r}")


def normalize_media_type(declared: str) -> str:
    return declared.split(";", 1)[0].strip().lower()


def family_from_media_type(declared: str) -> DetectedFamily | None:
    return _DECLARED_FAMILIES.get(normalize_media_type(declared))


def family_from_filename(filename: str) -> DetectedFamily | None:
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in name:
        return None
    suffix = "." + name.rsplit(".", 1)[-1].lower()
    return _EXTENSION_FAMILIES.get(suffix)


def media_type_for(family: DetectedFamily) -> str:
    match family:
        case (
            DetectedFamily.PNG
            | DetectedFamily.JPEG
            | DetectedFamily.PDF
            | DetectedFamily.JSON
            | DetectedFamily.YAML
            | DetectedFamily.MARKDOWN
            | DetectedFamily.TEXT
            | DetectedFamily.ZIP
            | DetectedFamily.ELF
            | DetectedFamily.PE
            | DetectedFamily.OLE
            | DetectedFamily.SVG
            | DetectedFamily.UNKNOWN
        ):
            return _MEDIA_TYPES[family]
        case _:
            return _assert_never_family(family)


def detect_family(
    payload: bytes,
    *,
    declared_media_type: str | None = None,
    original_filename: str | None = None,
) -> DetectionResult:
    offset_family, offset_magic = _offset_zero_magic(payload)
    embedded = _embedded_magics(payload, ignore=offset_magic)
    polyglot = bool(offset_magic and embedded)
    if offset_family is not None:
        magics = (offset_magic,) + embedded if offset_magic else embedded
        return DetectionResult(
            family=offset_family,
            media_type=media_type_for(offset_family),
            polyglot=polyglot,
            magics=magics,
        )

    declared_family = family_from_media_type(declared_media_type or "")
    extension_family = family_from_filename(original_filename or "")
    text_family = _detect_text_family(payload, declared_family, extension_family)
    magics = embedded
    if text_family is None:
        return DetectionResult(
            family=DetectedFamily.UNKNOWN,
            media_type=media_type_for(DetectedFamily.UNKNOWN),
            polyglot=bool(embedded),
            magics=magics,
        )
    return DetectionResult(
        family=text_family,
        media_type=media_type_for(text_family),
        polyglot=bool(embedded) and text_family.value in _TEXT_FAMILIES,
        magics=magics,
    )


def _offset_zero_magic(payload: bytes) -> tuple[DetectedFamily | None, str]:
    if payload.startswith(_PNG):
        return DetectedFamily.PNG, "png"
    if payload.startswith(_JPEG):
        return DetectedFamily.JPEG, "jpeg"
    if payload.startswith(_PDF):
        return DetectedFamily.PDF, "pdf"
    if payload.startswith((_ZIP, _ZIP_EMPTY, _ZIP_SPAN)):
        return DetectedFamily.ZIP, "zip"
    if payload.startswith(_ELF):
        return DetectedFamily.ELF, "elf"
    if payload.startswith(_OLE):
        return DetectedFamily.OLE, "ole"
    if payload.startswith(_PE):
        return DetectedFamily.PE, "pe"
    return None, ""


def _embedded_magics(payload: bytes, *, ignore: str) -> tuple[str, ...]:
    found: list[str] = []
    seen: set[str] = set()
    start = 1 if ignore else 0
    checks = (
        ("png", _PNG),
        ("jpeg", _JPEG),
        ("pdf", _PDF),
        ("zip", _ZIP),
        ("zip", _ZIP_EMPTY),
        ("elf", _ELF),
        ("ole", _OLE),
    )
    for name, magic in checks:
        if name == ignore or name in seen:
            continue
        if payload.find(magic, start) != -1:
            found.append(name)
            seen.add(name)
    return tuple(found)


def _detect_text_family(
    payload: bytes,
    declared_family: DetectedFamily | None,
    extension_family: DetectedFamily | None,
) -> DetectedFamily | None:
    if b"\x00" in payload:
        return None
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    stripped = text.lstrip("\ufeff").lstrip()
    lower = stripped.lower()
    if lower.startswith("<svg") or (lower.startswith("<?xml") and "<svg" in lower[:512]):
        return DetectedFamily.SVG
    if stripped.startswith(("{", "[")):
        return DetectedFamily.JSON
    hint = declared_family or extension_family
    if hint is DetectedFamily.YAML:
        return DetectedFamily.YAML
    if hint is DetectedFamily.MARKDOWN:
        return DetectedFamily.MARKDOWN
    if hint is DetectedFamily.JSON:
        return DetectedFamily.JSON
    if hint is DetectedFamily.TEXT:
        return DetectedFamily.TEXT
    return DetectedFamily.TEXT
