from __future__ import annotations

import hashlib
import json
from typing import Any

from .contracts import MAX_PACKET_BYTES, ResponsePacket


class ResponsePacketError(ValueError):
    """A response packet is missing required fields or exceeds policy limits."""


def canonical_json(value: dict[str, Any] | list[Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def fingerprint_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_packet_bytes(packet: ResponsePacket) -> bytes:
    body = packet.to_dict()
    body.pop("content_sha256", None)
    encoded = canonical_json(body)
    if len(encoded) > MAX_PACKET_BYTES:
        raise ResponsePacketError(f"Response packet exceeds the {MAX_PACKET_BYTES} byte limit.")
    return encoded


def fingerprint_packet(packet: ResponsePacket) -> str:
    return fingerprint_bytes(canonical_packet_bytes(packet))
