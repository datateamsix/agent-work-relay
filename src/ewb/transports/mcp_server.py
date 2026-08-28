from __future__ import annotations

from typing import Any

from ..factory import build_service


def create_server() -> Any:
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:
        raise RuntimeError("Install the MCP transport with: uv sync --extra mcp") from exc

    server = MCPServer("Engineering Work Broker")
    service = build_service()

    @server.tool()
    def submit_prompt_for_planning(
        markdown: str,
        sender: str,
        recipient: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Validate, record, wrap, and route a Markdown work order in PLAN_ONLY mode."""

        receipt = service.submit_prompt_for_planning(
            markdown=markdown,
            sender=sender,
            recipient=recipient,
            idempotency_key=idempotency_key,
        )
        return receipt.to_dict()

    return server


def run() -> None:
    create_server().run()


if __name__ == "__main__":
    run()
