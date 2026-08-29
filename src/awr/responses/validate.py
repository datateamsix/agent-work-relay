from __future__ import annotations

import json
from typing import Any

from ..artifacts.contracts import ArtifactPurpose, ArtifactReference
from ..decorators import DirectiveError
from .canonical import ResponsePacketError, fingerprint_packet
from .contracts import (
    FORBIDDEN_INLINE_KEYS,
    MAX_BODY_BYTES,
    MAX_COLLECTION,
    MAX_EVIDENCE_REFS,
    MAX_STRING_BYTES,
    RESPONSE_AUTHORITY,
    RESPONSE_SCHEMA,
    ResponsePacket,
    ResponseType,
    ReviewOutcome,
    assert_never_response_type,
)

_COMMON_REQUIRED = (
    "schema",
    "response_type",
    "work_order_id",
    "in_reply_to",
    "idempotency_key",
    "source_input_sha256",
    "created_at",
)
_COMMON_OPTIONAL = (
    "authority",
    "payload",
    "evidence_refs",
    "message_id",
    "correlation_id",
    "executor_run_id",
    "actor",
    "content_sha256",
)
_ALLOWED_TOP = frozenset(_COMMON_REQUIRED + _COMMON_OPTIONAL)


def parse_response_packet(payload: dict[str, Any]) -> ResponsePacket:
    if not isinstance(payload, dict):
        raise ResponsePacketError("Response packet must be an object.")
    unknown = set(payload) - _ALLOWED_TOP
    if unknown:
        raise ResponsePacketError(f"Unknown response fields: {sorted(unknown)}.")
    for field in _COMMON_REQUIRED:
        if field not in payload:
            raise ResponsePacketError(f"Missing required response field: {field}.")
    schema = _require_string(payload["schema"], "schema", MAX_STRING_BYTES)
    if schema != RESPONSE_SCHEMA:
        raise ResponsePacketError("Response schema must be awr.response/v1.")
    try:
        response_type = ResponseType(str(payload["response_type"]))
    except ValueError as exc:
        raise ResponsePacketError("Unknown response_type.") from exc
    authority = payload.get("authority", RESPONSE_AUTHORITY)
    authority_text = _require_string(authority, "authority", MAX_STRING_BYTES)
    if authority_text != RESPONSE_AUTHORITY:
        raise ResponsePacketError(
            "Responses never grant execution, merge, or deployment authority."
        )
    raw_payload = payload.get("payload") or {}
    if not isinstance(raw_payload, dict):
        raise ResponsePacketError("payload must be an object.")
    _reject_inline_blobs(raw_payload)
    typed = _validate_payload(response_type, raw_payload)
    refs = _parse_evidence(payload.get("evidence_refs") or [])
    packet = ResponsePacket(
        schema=schema,
        response_type=response_type,
        work_order_id=_require_string(payload["work_order_id"], "work_order_id", MAX_STRING_BYTES),
        in_reply_to=_require_string(payload["in_reply_to"], "in_reply_to", MAX_STRING_BYTES),
        idempotency_key=_require_string(
            payload["idempotency_key"], "idempotency_key", MAX_STRING_BYTES
        ),
        source_input_sha256=_digest(payload["source_input_sha256"], "source_input_sha256"),
        created_at=_require_string(payload["created_at"], "created_at", MAX_STRING_BYTES),
        authority=authority_text,
        payload=typed,
        evidence_refs=refs,
        message_id=_optional_string(payload.get("message_id"), "message_id"),
        correlation_id=_optional_string(payload.get("correlation_id"), "correlation_id"),
        executor_run_id=_optional_string(payload.get("executor_run_id"), "executor_run_id"),
        actor=_optional_string(payload.get("actor"), "actor"),
    )
    digest = fingerprint_packet(packet)
    declared = payload.get("content_sha256")
    if declared is not None and _digest(declared, "content_sha256") != digest:
        raise ResponsePacketError("content_sha256 does not match the canonical packet.")
    return ResponsePacket(
        schema=packet.schema,
        response_type=packet.response_type,
        work_order_id=packet.work_order_id,
        in_reply_to=packet.in_reply_to,
        idempotency_key=packet.idempotency_key,
        source_input_sha256=packet.source_input_sha256,
        created_at=packet.created_at,
        authority=packet.authority,
        payload=packet.payload,
        evidence_refs=packet.evidence_refs,
        message_id=packet.message_id,
        correlation_id=packet.correlation_id,
        executor_run_id=packet.executor_run_id,
        actor=packet.actor,
        content_sha256=digest,
    )


def parse_response_markdown(markdown: str) -> ResponsePacket:
    lines = markdown.splitlines()
    start = next((index for index, line in enumerate(lines) if line.strip()), None)
    if start is None or lines[start].strip() != "@response":
        raise DirectiveError("Exactly one @response decorator must lead the document.")
    marks = [line.strip() for line in lines if line.strip() == "@response"]
    if len(marks) != 1:
        raise DirectiveError("Exactly one @response decorator must lead the document.")
    open_index = None
    close_index = None
    for index in range(start + 1, len(lines)):
        if not lines[index].strip():
            continue
        if lines[index].strip() != "---":
            raise DirectiveError("An @response document must open YAML frontmatter.")
        open_index = index
        break
    if open_index is None:
        raise DirectiveError("An @response document must include YAML frontmatter.")
    for index in range(open_index + 1, len(lines)):
        if lines[index].strip() == "---":
            close_index = index
            break
    if close_index is None:
        raise DirectiveError("An @response document must close YAML frontmatter.")
    mapping = _parse_awr_block("\n".join(lines[open_index + 1 : close_index]))
    body = "\n".join(lines[close_index + 1 :]).strip("\n")
    _assign_markdown_body(mapping, body)
    payload_keys = set(mapping) - {
        "schema",
        "response_type",
        "work_order_id",
        "in_reply_to",
        "idempotency_key",
        "source_input_sha256",
        "created_at",
        "authority",
        "message_id",
        "correlation_id",
        "executor_run_id",
        "actor",
        "content_sha256",
        "evidence_refs",
    }
    packet: dict[str, Any] = {key: mapping[key] for key in mapping if key not in payload_keys}
    if "evidence_refs" in packet:
        packet["evidence_refs"] = _parse_markdown_evidence(packet["evidence_refs"])
    payload: dict[str, Any] = {key: mapping[key] for key in payload_keys}
    if "body_sha256" in payload:
        payload["content_sha256"] = payload.pop("body_sha256")
    for numeric in ("percent", "ledger_sequence"):
        raw = payload.get(numeric)
        if isinstance(raw, str) and raw.isdigit():
            payload[numeric] = int(raw)
    if (
        str(packet.get("response_type")) == "execution.acknowledged"
        and packet.get("executor_run_id")
        and "executor_run_id" not in payload
    ):
        payload["executor_run_id"] = packet["executor_run_id"]
    packet["payload"] = payload
    return parse_response_packet(packet)


def _validate_payload(response_type: ResponseType, payload: dict[str, Any]) -> dict[str, Any]:
    match response_type:
        case ResponseType.RECEIPT_ACCEPTED:
            return _object(
                payload,
                required=("receipt_type", "status", "content_sha256"),
                optional=("ledger_sequence", "bundle_sha256", "duplicate"),
            )
        case ResponseType.PLAN_COMPLETED:
            return _object(
                payload,
                required=("content", "content_sha256"),
                optional=("title", "plan_id"),
                body_fields=("content",),
            )
        case ResponseType.QUESTION_BLOCKED:
            questions = payload.get("questions")
            if not isinstance(questions, list) or not questions:
                raise ResponsePacketError("question.blocked requires a questions collection.")
            if len(questions) > MAX_COLLECTION:
                raise ResponsePacketError("questions exceed the collection limit.")
            cleaned = []
            for item in questions:
                if not isinstance(item, dict):
                    raise ResponsePacketError("Each question must be an object.")
                cleaned.append(
                    _object(item, required=("id", "text"), optional=(), body_fields=("text",))
                )
            return {"questions": cleaned}
        case ResponseType.EXECUTION_ACKNOWLEDGED:
            return _object(
                payload,
                required=("executor", "executor_run_id"),
                optional=("message",),
            )
        case ResponseType.EXECUTION_PROGRESS:
            typed = _object(payload, required=("message",), optional=("percent",))
            if "percent" in typed:
                percent = typed["percent"]
                if not isinstance(percent, int) or percent < 0 or percent > 100:
                    raise ResponsePacketError("percent must be an integer from 0 to 100.")
            return typed
        case ResponseType.EXECUTION_COMPLETED:
            return _object(
                payload,
                required=("summary",),
                optional=("content_sha256",),
                body_fields=("summary",),
            )
        case ResponseType.EXECUTION_FAILED:
            return _object(payload, required=("error_type", "message"), optional=())
        case ResponseType.REVIEW_COMPLETED:
            typed = _object(
                payload,
                required=("outcome", "rationale"),
                optional=(),
                body_fields=("rationale",),
            )
            try:
                ReviewOutcome(str(typed["outcome"]))
            except ValueError as exc:
                raise ResponsePacketError("Unknown review outcome.") from exc
            return typed
        case _:
            return assert_never_response_type(response_type)


def _object(
    payload: dict[str, Any],
    *,
    required: tuple[str, ...],
    optional: tuple[str, ...],
    body_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    allowed = frozenset(required + optional)
    unknown = set(payload) - allowed
    if unknown:
        raise ResponsePacketError(f"Unknown payload fields: {sorted(unknown)}.")
    result: dict[str, Any] = {}
    for key in required:
        if key not in payload:
            raise ResponsePacketError(f"Missing payload field: {key}.")
        if key.endswith("sha256"):
            result[key] = _digest(payload[key], key)
            continue
        limit = MAX_BODY_BYTES if key in body_fields else MAX_STRING_BYTES
        result[key] = (
            payload[key] if key == "percent" else _require_string(payload[key], key, limit)
        )
    for key in optional:
        if key not in payload or payload[key] is None:
            continue
        if key.endswith("sha256"):
            result[key] = _digest(payload[key], key)
            continue
        if key in {"ledger_sequence", "percent"}:
            if not isinstance(payload[key], int):
                raise ResponsePacketError(f"{key} must be an integer.")
            result[key] = payload[key]
            continue
        if key == "duplicate":
            if not isinstance(payload[key], bool):
                raise ResponsePacketError("duplicate must be a boolean.")
            result[key] = payload[key]
            continue
        limit = MAX_BODY_BYTES if key in body_fields else MAX_STRING_BYTES
        result[key] = _require_string(payload[key], key, limit)
    return result


def _parse_evidence(raw: object) -> tuple[ArtifactReference, ...]:
    if not isinstance(raw, list):
        raise ResponsePacketError("evidence_refs must be a list.")
    if len(raw) > MAX_EVIDENCE_REFS:
        raise ResponsePacketError("evidence_refs exceed the collection limit.")
    refs: list[ArtifactReference] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ResponsePacketError("Each evidence ref must be an object.")
        unknown = set(item) - {
            "artifact_id",
            "purpose",
            "byte_length",
            "sha256",
            "detected_media_type",
            "safe_filename",
        }
        if unknown:
            raise ResponsePacketError(f"Unknown evidence ref fields: {sorted(unknown)}.")
        try:
            purpose = ArtifactPurpose(str(item["purpose"]))
        except (KeyError, ValueError) as exc:
            raise ResponsePacketError("evidence_refs require a known purpose.") from exc
        refs.append(
            ArtifactReference(
                artifact_id=_require_string(item["artifact_id"], "artifact_id", MAX_STRING_BYTES),
                purpose=purpose,
                byte_length=int(item["byte_length"]),
                sha256=_digest(item["sha256"], "sha256"),
                detected_media_type=_optional_string(
                    item.get("detected_media_type"), "detected_media_type"
                ),
                safe_filename=_require_string(
                    item["safe_filename"], "safe_filename", MAX_STRING_BYTES
                ),
            )
        )
    return tuple(refs)


def _reject_inline_blobs(payload: dict[str, Any]) -> None:
    for key in payload:
        if key in FORBIDDEN_INLINE_KEYS:
            raise ResponsePacketError(
                "Large logs, diffs, reports, and visual evidence must be artifact references."
            )


def _require_string(value: object, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value:
        raise ResponsePacketError(f"{name} must be a non-empty string.")
    if len(value.encode("utf-8")) > limit:
        raise ResponsePacketError(f"{name} exceeds the {limit} byte limit.")
    return value


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, name, MAX_STRING_BYTES)


def _digest(value: object, name: str) -> str:
    text = _require_string(value, name, MAX_STRING_BYTES)
    digest = text.removeprefix("sha256:")
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ResponsePacketError(f"{name} must be a SHA-256 hex digest.")
    return digest


def _parse_markdown_evidence(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str) or not value:
        raise ResponsePacketError("evidence_refs must be a list.")
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ResponsePacketError("evidence_refs must be a JSON array.") from exc
    if not isinstance(loaded, list):
        raise ResponsePacketError("evidence_refs must be a list.")
    return loaded


def _assign_markdown_body(mapping: dict[str, Any], body: str) -> None:
    """Map the prose body onto the AS-03 payload field for that response type.

    Rendered packets keep long text in the Markdown body. Types that do not
    carry a body field ignore leftover prose so optional headings do not become
    unknown payload keys.
    """
    if not body:
        return
    response_type = str(mapping.get("response_type") or "")
    title = mapping.get("title")
    prefix = f"# {title}\n\n" if isinstance(title, str) and title else ""
    text = body[len(prefix) :] if prefix and body.startswith(prefix) else body
    headings = {
        "plan.completed": ("content", None),
        "execution.completed": ("summary", "# Execution completed\n\n"),
        "execution.progress": ("message", "# Execution progress\n\n"),
        "execution.failed": ("message", "# Execution failed\n\n"),
        "review.completed": (
            "rationale",
            f"# Review {mapping['outcome']}\n\n" if mapping.get("outcome") else None,
        ),
    }
    target = headings.get(response_type)
    if target is None:
        return
    field, heading = target
    if field in mapping:
        return
    if heading and text.startswith(heading):
        text = text[len(heading) :]
    mapping[field] = text


def _parse_awr_block(text: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    in_awr = False
    awr_indent = 0
    for raw in text.splitlines():
        if not in_awr:
            stripped = raw.lstrip()
            if stripped == "awr:" or stripped.startswith("awr:"):
                in_awr = True
                awr_indent = len(raw) - len(stripped)
            continue
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent <= awr_indent:
            break
        stripped = raw.strip()
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        value = value.strip().strip('"').strip("'")
        if value in {"", "|", ">"}:
            continue
        mapping[key.strip()] = value
    if not mapping:
        raise ResponsePacketError("An @response envelope must contain an awr mapping.")
    return mapping
