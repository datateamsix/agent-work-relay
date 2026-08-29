from __future__ import annotations

from typing import Any

from ..factory import build_service
from ..service import BrokerService


def create_server(service: BrokerService | None = None) -> Any:
    try:
        from mcp.server import MCPServer
    except ImportError as exc:
        raise RuntimeError("Install the MCP transport with: uv sync --extra mcp") from exc

    server = MCPServer(
        "Agent Work Relay",
        instructions=(
            "Submit decorated Markdown work orders for plan-only Cursor runs. "
            "Use begin_artifact_intake plus PUT /v1/artifacts/{id}/content for binaries. "
            "Do not request execution."
        ),
    )
    broker = service or build_service()

    @server.tool(
        name="submit_prompt_for_planning",
        title="Submit prompt for planning",
        description="Validate, record, wrap, and route a Markdown work order in PLAN_ONLY mode.",
        meta=_security_meta("awr:plan"),
    )
    def submit_prompt_for_planning(
        markdown: str,
        sender: str,
        recipient: str,
        repository_url: str | None = None,
        base_ref: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        receipt = broker.submit_prompt_for_planning(
            markdown=markdown,
            sender=sender,
            recipient=recipient,
            repository_url=repository_url,
            base_ref=base_ref,
            idempotency_key=idempotency_key,
        )
        return receipt.to_dict()

    @server.tool(
        name="refresh_planning",
        title="Refresh planning",
        description="Refresh a Cursor planning run and capture its terminal plan exactly once.",
        meta=_security_meta("awr:refresh"),
    )
    def refresh_planning(work_order_id: str) -> dict[str, Any]:
        return broker.refresh_planning(work_order_id).to_dict()

    @server.tool(
        name="get_plan",
        title="Get plan",
        description="Return the completed, fingerprinted plan for a work order.",
        meta=_security_meta("awr:read"),
    )
    def get_plan(work_order_id: str) -> dict[str, Any]:
        return broker.get_plan(work_order_id).to_dict()

    @server.tool(
        name="get_work_order_timeline",
        title="Get work order timeline",
        description="Return the ordered receipt ledger for a work order.",
        meta=_security_meta("awr:read"),
    )
    def get_work_order_timeline(work_order_id: str) -> list[dict[str, Any]]:
        return broker.get_work_order_timeline(work_order_id)

    @server.tool(
        name="begin_artifact_intake",
        title="Begin artifact intake",
        description="Declare a supporting artifact and issue a one-time authenticated upload ticket.",
        meta=_security_meta("awr:plan"),
    )
    def begin_artifact_intake(
        original_filename: str,
        declared_media_type: str,
        purpose: str,
        idempotency_key: str,
        sender: str | None = None,
        expected_byte_length: int | None = None,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        return broker.begin_artifact_intake(
            owner=sender,
            original_filename=original_filename,
            declared_media_type=declared_media_type,
            purpose=purpose,
            idempotency_key=idempotency_key,
            expected_byte_length=expected_byte_length,
            expected_sha256=expected_sha256,
        )

    @server.tool(
        name="finalize_artifact_upload",
        title="Finalize artifact upload",
        description="Inspect a quarantined artifact and return its security status.",
        meta=_security_meta("awr:plan"),
    )
    def finalize_artifact_upload(artifact_id: str, sender: str | None = None) -> dict[str, Any]:
        return broker.finalize_artifact_upload(artifact_id, actor=sender)

    @server.tool(
        name="get_artifact_status",
        title="Get artifact status",
        description="Return artifact metadata and the latest security receipt. No paths or bytes.",
        meta=_security_meta("awr:read"),
    )
    def get_artifact_status(artifact_id: str, sender: str | None = None) -> dict[str, Any]:
        return broker.get_artifact_status(artifact_id, actor=sender)

    @server.tool(
        name="submit_work_bundle_for_planning",
        title="Submit work bundle for planning",
        description=(
            "Accept a decorated Markdown work order plus immutable CLEAN artifact IDs. "
            "Do not send file bytes or remote URLs."
        ),
        meta=_security_meta("awr:plan"),
    )
    def submit_work_bundle_for_planning(
        markdown: str,
        recipient: str,
        sender: str | None = None,
        repository_url: str | None = None,
        base_ref: str | None = None,
        idempotency_key: str | None = None,
        artifact_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        receipt = broker.submit_work_bundle_for_planning(
            markdown=markdown,
            sender=sender,
            recipient=recipient,
            repository_url=repository_url,
            base_ref=base_ref,
            idempotency_key=idempotency_key,
            artifact_ids=artifact_ids,
        )
        return receipt.to_dict()

    @server.tool(
        name="get_work_order_artifacts",
        title="Get work order artifacts",
        description="Return immutable artifact references for a work order.",
        meta=_security_meta("awr:read"),
    )
    def get_work_order_artifacts(work_order_id: str) -> list[dict[str, Any]]:
        return broker.get_work_order_artifacts(work_order_id)

    @server.tool(
        name="submit_response",
        title="Submit response",
        description="Accept an @response packet. Responses never grant authority.",
        meta=_security_meta("awr:response"),
    )
    def submit_response(markdown: str, sender: str | None = None) -> dict[str, Any]:
        return broker.submit_response(markdown=markdown, actor=sender)

    @server.tool(
        name="record_decision",
        title="Record decision",
        description="Record an authenticated approval, rejection, cancellation, or closure.",
        meta=_security_meta("awr:decide"),
    )
    def record_decision(
        decision_type: str,
        work_order_id: str,
        target_id: str,
        target_sha256: str,
        idempotency_key: str,
        permitted_action: str,
        rationale: str,
        sender: str | None = None,
        scope: str = "restricted",
        target_kind: str = "plan",
        expires_at: str | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        return broker.record_decision(
            decision_type=decision_type,
            work_order_id=work_order_id,
            actor=sender,
            target_id=target_id,
            target_sha256=target_sha256,
            idempotency_key=idempotency_key,
            permitted_action=permitted_action,
            rationale=rationale,
            scope=scope,
            target_kind=target_kind,
            expires_at=expires_at,
            expected_version=expected_version,
        )

    @server.tool(
        name="list_pending_actions",
        title="List pending actions",
        description="List approvals, reviews, or work waiting on an authorized participant.",
        meta=_security_meta("awr:read"),
    )
    def list_pending_actions(
        work_order_id: str | None = None, sender: str | None = None
    ) -> list[dict[str, Any]]:
        return broker.list_pending_actions(work_order_id, actor=sender)

    @server.tool(
        name="get_work_order",
        title="Get work order",
        description="Return the current projection, immutable refs, and pending actions.",
        meta=_security_meta("awr:read"),
    )
    def get_work_order(work_order_id: str, sender: str | None = None) -> dict[str, Any]:
        return broker.get_work_order(work_order_id, actor=sender)

    return server


def _security_meta(scope: str) -> dict[str, Any]:
    return {
        "securitySchemes": [
            {
                "type": "oauth2",
                "scheme": "bearer",
                "scopes": [scope],
            }
        ],
        "requiredScopes": [scope],
    }


def run() -> None:
    create_server().run()


if __name__ == "__main__":
    run()
