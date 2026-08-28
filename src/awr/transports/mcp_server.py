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
            "Use refresh_planning to capture the terminal plan. Do not request execution."
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
