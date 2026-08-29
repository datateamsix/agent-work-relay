from __future__ import annotations

import json

from .canonical import fingerprint_packet
from .contracts import (
    RENDER_TEMPLATE_ID,
    RENDER_TEMPLATE_VERSION,
    ResponsePacket,
    ResponseType,
    assert_never_response_type,
)


def render_response_markdown(packet: ResponsePacket) -> str:
    digest = packet.content_sha256 or fingerprint_packet(packet)
    fields = [
        f"  schema: {packet.schema}",
        f"  response_type: {packet.response_type.value}",
        f"  work_order_id: {packet.work_order_id}",
        f"  in_reply_to: {packet.in_reply_to}",
        f"  idempotency_key: {packet.idempotency_key}",
        f"  source_input_sha256: sha256:{packet.source_input_sha256}",
        f"  created_at: {packet.created_at}",
        f"  authority: {packet.authority}",
        f"  content_sha256: sha256:{digest}",
    ]
    if packet.executor_run_id:
        fields.append(f"  executor_run_id: {packet.executor_run_id}")
    if packet.correlation_id:
        fields.append(f"  correlation_id: {packet.correlation_id}")
    if packet.evidence_refs:
        fields.append(
            "  evidence_refs: "
            + json.dumps([item.to_dict() for item in packet.evidence_refs], separators=(",", ":"))
        )
    fields.extend(_payload_fields(packet))
    body = _body(packet)
    return "@response\n---\nawr:\n" + "\n".join(fields) + "\n---\n\n" + body + "\n"


def _payload_fields(packet: ResponsePacket) -> list[str]:
    skip = {"content", "summary", "rationale", "message", "questions"}
    lines: list[str] = []
    for key, value in packet.payload.items():
        if key in skip:
            continue
        yaml_key = "body_sha256" if key == "content_sha256" else key
        if isinstance(value, bool):
            lines.append(f"  {yaml_key}: {'true' if value else 'false'}")
            continue
        if isinstance(value, int):
            lines.append(f"  {yaml_key}: {value}")
            continue
        text = str(value)
        if "sha256" in key:
            text = f"sha256:{text.removeprefix('sha256:')}"
        lines.append(f"  {yaml_key}: {text}")
    return lines


def _body(packet: ResponsePacket) -> str:
    payload = packet.payload
    match packet.response_type:
        case ResponseType.RECEIPT_ACCEPTED:
            return (
                f"# Receipt accepted\n\n"
                f"- Status: {payload['status']}\n"
                f"- Receipt: {payload['receipt_type']}"
            )
        case ResponseType.PLAN_COMPLETED:
            title = payload.get("title")
            if title:
                return f"# {title}\n\n{payload['content']}"
            return str(payload["content"])
        case ResponseType.QUESTION_BLOCKED:
            lines = ["# Blocking questions", ""]
            for item in payload["questions"]:
                lines.append(f"- {item['id']}: {item['text']}")
            return "\n".join(lines)
        case ResponseType.EXECUTION_ACKNOWLEDGED:
            return f"# Execution acknowledged\n\nRun `{payload['executor_run_id']}` was accepted."
        case ResponseType.EXECUTION_PROGRESS:
            return f"# Execution progress\n\n{payload['message']}"
        case ResponseType.EXECUTION_COMPLETED:
            return f"# Execution completed\n\n{payload['summary']}"
        case ResponseType.EXECUTION_FAILED:
            return f"# Execution failed\n\n{payload['message']}"
        case ResponseType.REVIEW_COMPLETED:
            return f"# Review {payload['outcome']}\n\n{payload['rationale']}"
        case _:
            return assert_never_response_type(packet.response_type)


def template_identity() -> tuple[str, str]:
    return RENDER_TEMPLATE_ID, RENDER_TEMPLATE_VERSION
