from __future__ import annotations

from .canonical import fingerprint_bytes
from .contracts import POLICY_VERSION, RENDER_TEMPLATE_ID, RENDER_TEMPLATE_VERSION


def replay_cache_key(
    *,
    sender: str,
    recipient: str,
    directive: str,
    parent: str | None,
    repository_url: str,
    base_ref: str,
    content_sha256: str,
    bundle_sha256: str = "",
) -> str:
    return _digest(
        (
            "replay",
            sender,
            recipient,
            directive,
            parent or "",
            repository_url,
            base_ref,
            content_sha256,
            bundle_sha256,
        )
    )


def security_receipt_cache_key(
    *,
    sha256: str,
    scanner_id: str,
    scanner_version: str,
    signature_version: str,
    policy_version: str = POLICY_VERSION,
) -> str:
    return _digest(
        ("security-receipt", sha256, scanner_id, scanner_version, signature_version, policy_version)
    )


def response_packet_cache_key(
    *,
    canonical_sha256: str,
    template_id: str = RENDER_TEMPLATE_ID,
    template_version: str = RENDER_TEMPLATE_VERSION,
) -> str:
    return _digest(("response-packet", canonical_sha256, template_id, template_version))


def etag_for_digest(digest: str) -> str:
    return f'"sha256:{digest}"'


def response_idempotency_cache_key(
    *,
    actor: str,
    operation: str,
    idempotency_key: str,
    packet_fingerprint: str,
) -> str:
    """Durable response replay key. Caching cannot bypass validation."""
    return _digest(
        (
            "response-idempotency",
            actor,
            operation,
            idempotency_key,
            packet_fingerprint.removeprefix("sha256:"),
        )
    )


def provider_run_cache_key(
    *,
    provider: str,
    agent_id: str,
    run_id: str,
    last_known_version: str,
) -> str:
    return _digest(("provider-run", provider, agent_id, run_id, last_known_version))


def _digest(parts: tuple[str, ...]) -> str:
    return fingerprint_bytes("|".join(parts).encode("utf-8"))
