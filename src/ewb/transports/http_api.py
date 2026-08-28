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
            repository_url=(
                str(payload["repository_url"]) if payload.get("repository_url") else None
            ),
            base_ref=str(payload["base_ref"]) if payload.get("base_ref") else None,
            idempotency_key=payload.get("idempotency_key"),
        )
        return receipt.to_dict()

    @app.post("/v1/planning/{work_order_id}/refresh")
    def refresh_planning(work_order_id: str) -> dict[str, Any]:
        return service.refresh_planning(work_order_id).to_dict()

    @app.get("/v1/planning/{work_order_id}/plan")
    def get_plan(work_order_id: str) -> dict[str, Any]:
        return service.get_plan(work_order_id).to_dict()

    @app.get("/v1/planning/{work_order_id}/timeline")
    def get_timeline(work_order_id: str) -> list[dict[str, Any]]:
        return service.get_work_order_timeline(work_order_id)

    return app
