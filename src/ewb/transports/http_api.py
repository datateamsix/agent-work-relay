from __future__ import annotations

from typing import Any

from ..service import BrokerService


def create_app(service: BrokerService) -> Any:
    try:
        from fastapi import FastAPI
    except ImportError as exc:
        raise RuntimeError("Install the HTTP transport with: uv sync --extra api") from exc

    app = FastAPI(title="Engineering Work Broker", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/planning")
    def submit_planning(payload: dict[str, Any]) -> dict[str, Any]:
        receipt = service.submit_prompt_for_planning(
            markdown=str(payload["markdown"]),
            sender=str(payload["sender"]),
            recipient=str(payload["recipient"]),
            idempotency_key=payload.get("idempotency_key"),
        )
        return receipt.to_dict()

    return app
