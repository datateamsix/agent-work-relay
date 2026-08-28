from __future__ import annotations

from typing import Any

from ..factory import build_service


def create_server() -> Any:
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:
        raise RuntimeError("Install the MCP transport with: uv sync --extra mcp") from exc

    server = MCPServer("Agent Work Relay")
    service = build_service()

    @server.tool()
    def submit_prompt_for_planning(
        markdown: str,
        sender: str,
        recipient: str,
        repository_url: str | None = None,
        base_ref: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Validate, record, wrap, and route a Markdown work order in PLAN_ONLY mode."""

        receipt = service.submit_prompt_for_planning(
            markdown=markdown,
            sender=sender,
            recipient=recipient,
            repository_url=repository_url,
            base_ref=base_ref,
            idempotency_key=idempotency_key,
        )
        return receipt.to_dict()

    @server.tool()
    def refresh_planning(work_order_id: str) -> dict[str, Any]:
        """Refresh a Cursor planning run and capture its terminal plan exactly once."""

        return service.refresh_planning(work_order_id).to_dict()

    @server.tool()
    def get_plan(work_order_id: str) -> dict[str, Any]:
        """Return the completed, fingerprinted plan for a work order."""

        return service.get_plan(work_order_id).to_dict()

    @server.tool()
    def get_work_order_timeline(work_order_id: str) -> list[dict[str, Any]]:
        """Return the ordered receipt ledger for a work order."""

        return service.get_work_order_timeline(work_order_id)

    return server


def run() -> None:
    create_server().run()


if __name__ == "__main__":
    run()
