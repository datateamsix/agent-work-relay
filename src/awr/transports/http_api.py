from __future__ import annotations

import io
from typing import Any

from ..artifacts.errors import ArtifactError, ArtifactTicketError
from ..service import BrokerService, WorkOrderValidationError


def create_app(service: BrokerService) -> Any:
    try:
        from fastapi import FastAPI, Header, Request
        from fastapi.responses import JSONResponse
    except ImportError as exc:
        raise RuntimeError("Install the HTTP transport with: uv sync --extra api") from exc

    app = FastAPI(title="Agent Work Relay", version="0.1.0")

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

    @app.get("/v1/planning/{work_order_id}/artifacts")
    def get_artifacts(work_order_id: str) -> list[dict[str, Any]]:
        return service.get_work_order_artifacts(work_order_id)

    @app.post("/v1/artifacts")
    def begin_artifact(payload: dict[str, Any]) -> dict[str, Any]:
        return service.begin_artifact_intake(
            owner=payload.get("sender") or payload.get("owner"),
            original_filename=str(payload["original_filename"]),
            declared_media_type=str(payload["declared_media_type"]),
            purpose=str(payload["purpose"]),
            idempotency_key=str(payload["idempotency_key"]),
            expected_byte_length=payload.get("expected_byte_length"),
            expected_sha256=payload.get("expected_sha256"),
        )

    @app.put("/v1/artifacts/{artifact_id}/content")
    async def upload_artifact(
        artifact_id: str,
        request: Request,
        x_awr_upload_token: str | None = Header(default=None),
    ) -> Any:
        if not x_awr_upload_token:
            return JSONResponse({"error": "Upload token required."}, status_code=400)
        body = await request.body()
        try:
            return service.upload_artifact_content(
                artifact_id, io.BytesIO(body), token=x_awr_upload_token
            )
        except ArtifactTicketError as exc:
            status = 409 if "spent" in str(exc).lower() else 403
            if "expired" in str(exc).lower():
                status = 410
            return JSONResponse({"error": str(exc)}, status_code=status)
        except (ArtifactError, WorkOrderValidationError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/v1/artifacts/{artifact_id}/finalize")
    def finalize_artifact(artifact_id: str) -> dict[str, Any]:
        return service.finalize_artifact_upload(artifact_id)

    @app.get("/v1/artifacts/{artifact_id}")
    def artifact_status(artifact_id: str) -> dict[str, Any]:
        return service.get_artifact_status(artifact_id)

    @app.post("/v1/work-bundles")
    def submit_bundle(payload: dict[str, Any]) -> dict[str, Any]:
        receipt = service.submit_work_bundle_for_planning(
            markdown=str(payload["markdown"]),
            sender=payload.get("sender"),
            recipient=str(payload["recipient"]),
            repository_url=(
                str(payload["repository_url"]) if payload.get("repository_url") else None
            ),
            base_ref=str(payload["base_ref"]) if payload.get("base_ref") else None,
            idempotency_key=payload.get("idempotency_key"),
            artifact_ids=payload.get("artifact_ids") or [],
        )
        return receipt.to_dict()

    @app.post("/v1/responses")
    def submit_response(payload: dict[str, Any]) -> Any:
        try:
            return service.submit_response(
                markdown=str(payload["markdown"]),
                actor=payload.get("sender"),
                expected_version=payload.get("expected_version"),
            )
        except WorkOrderValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/v1/decisions")
    def record_decision(payload: dict[str, Any]) -> Any:
        try:
            return service.record_decision(
                decision_type=str(payload["decision_type"]),
                work_order_id=str(payload["work_order_id"]),
                actor=payload.get("sender"),
                target_id=str(payload["target_id"]),
                target_sha256=str(payload["target_sha256"]),
                idempotency_key=str(payload["idempotency_key"]),
                permitted_action=str(payload["permitted_action"]),
                rationale=str(payload["rationale"]),
                scope=str(payload.get("scope") or "restricted"),
                target_kind=str(payload.get("target_kind") or "plan"),
                expires_at=str(payload["expires_at"]) if payload.get("expires_at") else None,
                expected_version=payload.get("expected_version"),
            )
        except WorkOrderValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/v1/work-orders/{work_order_id}")
    def get_work_order(work_order_id: str) -> Any:
        try:
            return service.get_work_order(work_order_id)
        except WorkOrderValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/v1/work-orders/{work_order_id}/pending")
    def list_pending(work_order_id: str) -> Any:
        try:
            return service.list_pending_actions(work_order_id)
        except WorkOrderValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    return app
