from __future__ import annotations

import io
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route

from ..artifacts.errors import ArtifactError, ArtifactTicketError
from ..auth.middleware import OAuthResourceMiddleware
from ..auth.tokens import TokenVerifier
from ..factory import build_service
from ..observability import configure_logging, log_event
from ..service import BrokerService, WorkOrderValidationError
from ..settings import Settings
from .http_cache import cached_json, no_store_json
from .mcp_server import create_server

_HOME_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent Work Relay</title>
  <style>
    :root { color-scheme: light dark; }
    body { font-family: ui-sans-serif, system-ui, sans-serif; margin: 0; padding: 2rem; line-height: 1.5; max-width: 44rem; }
    code { font-family: ui-monospace, monospace; }
    .card { border: 1px solid color-mix(in srgb, currentColor 20%, transparent); border-radius: 12px; padding: 1.25rem 1.5rem; }
    a { color: inherit; }
  </style>
</head>
<body>
  <main class="card">
    <h1>Agent Work Relay</h1>
    <p>Pass work between agents, not through humans. This service is an OAuth-protected MCP resource server for plan-only Cursor handoffs.</p>
    <ul>
      <li>Public health: <a href="/healthz"><code>/healthz</code></a></li>
      <li>MCP endpoint: <code>/mcp</code> (OAuth 2.1 bearer required)</li>
      <li>Protected resource metadata: <a href="/.well-known/oauth-protected-resource/mcp"><code>/.well-known/oauth-protected-resource/mcp</code></a></li>
    </ul>
    <p>ChatGPT and other MCP clients must complete authorization-code + PKCE before calling tools. Planning requests stay read-only. This page does not list work orders or secrets.</p>
  </main>
</body>
</html>
"""


def create_app(
    settings: Settings | None = None,
    service: BrokerService | None = None,
) -> Starlette:
    resolved = settings or Settings.from_env()
    resolved.validate()
    logger = configure_logging(resolved.log_level)
    broker = service or build_service()
    mcp = create_server(broker)
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=_transport_security(resolved),
        host="0.0.0.0",
    )
    verifier = TokenVerifier(resolved)

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        log_event(
            logger,
            "server.start",
            env=resolved.env,
            storage=resolved.storage,
            auth_mode=resolved.auth_mode,
        )
        async with mcp.session_manager.run():
            yield
        log_event(logger, "server.stop")

    async def home(_: Request) -> Response:
        return HTMLResponse(_HOME_HTML)

    async def healthz(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def protected_resource(_: Request) -> Response:
        return JSONResponse(resolved.protected_resource_metadata())

    async def submit_planning(request: Request) -> Response:
        payload = await request.json()
        try:
            receipt = broker.submit_prompt_for_planning(
                markdown=str(payload["markdown"]),
                sender=str(payload["sender"]),
                recipient=str(payload["recipient"]),
                repository_url=(
                    str(payload["repository_url"]) if payload.get("repository_url") else None
                ),
                base_ref=str(payload["base_ref"]) if payload.get("base_ref") else None,
                idempotency_key=payload.get("idempotency_key"),
            )
        except (WorkOrderValidationError, KeyError, TypeError, ValueError) as exc:
            return no_store_json({"error": str(exc)}, status_code=400)
        return no_store_json(receipt.to_dict())

    async def refresh_planning(request: Request) -> Response:
        work_order_id = request.path_params["work_order_id"]
        try:
            result = broker.refresh_planning(work_order_id)
        except WorkOrderValidationError as exc:
            return no_store_json({"error": str(exc)}, status_code=400)
        return no_store_json(result.to_dict())

    async def get_plan(request: Request) -> Response:
        work_order_id = request.path_params["work_order_id"]
        try:
            result = broker.get_plan(work_order_id)
        except WorkOrderValidationError as exc:
            return no_store_json({"error": str(exc)}, status_code=400)
        return cached_json(request, result.to_dict())

    async def get_timeline(request: Request) -> Response:
        work_order_id = request.path_params["work_order_id"]
        try:
            result = broker.get_work_order_timeline(work_order_id)
        except WorkOrderValidationError as exc:
            return no_store_json({"error": str(exc)}, status_code=400)
        return cached_json(request, result)

    async def begin_artifact(request: Request) -> Response:
        payload = await request.json()
        try:
            result = broker.begin_artifact_intake(
                owner=payload.get("sender") or payload.get("owner"),
                original_filename=str(payload["original_filename"]),
                declared_media_type=str(payload["declared_media_type"]),
                purpose=str(payload["purpose"]),
                idempotency_key=str(payload["idempotency_key"]),
                expected_byte_length=payload.get("expected_byte_length"),
                expected_sha256=payload.get("expected_sha256"),
            )
        except (WorkOrderValidationError, ArtifactError, KeyError, TypeError, ValueError) as exc:
            return no_store_json({"error": str(exc)}, status_code=400)
        return no_store_json(result)

    async def upload_artifact(request: Request) -> Response:
        artifact_id = request.path_params["artifact_id"]
        token = request.headers.get("x-awr-upload-token")
        if not token:
            return no_store_json({"error": "Upload token required."}, status_code=400)
        body = await request.body()
        try:
            result = broker.upload_artifact_content(artifact_id, io.BytesIO(body), token=token)
        except ArtifactTicketError as exc:
            status = 409 if "spent" in str(exc).lower() else 403
            if "expired" in str(exc).lower():
                status = 410
            if "missing" in str(exc).lower():
                status = 404
            return no_store_json({"error": str(exc)}, status_code=status)
        except (ArtifactError, WorkOrderValidationError, KeyError) as exc:
            return no_store_json({"error": str(exc)}, status_code=400)
        return no_store_json(result)

    async def finalize_artifact(request: Request) -> Response:
        artifact_id = request.path_params["artifact_id"]
        try:
            result = broker.finalize_artifact_upload(artifact_id)
        except KeyError as exc:
            return no_store_json({"error": str(exc)}, status_code=404)
        except (ArtifactError, WorkOrderValidationError) as exc:
            return no_store_json({"error": str(exc)}, status_code=400)
        return no_store_json(result)

    async def artifact_status(request: Request) -> Response:
        artifact_id = request.path_params["artifact_id"]
        try:
            result = broker.get_artifact_status(artifact_id)
        except KeyError as exc:
            return no_store_json({"error": str(exc)}, status_code=404)
        except WorkOrderValidationError as exc:
            return no_store_json({"error": str(exc)}, status_code=400)
        return cached_json(request, result)

    async def submit_bundle(request: Request) -> Response:
        payload = await request.json()
        try:
            receipt = broker.submit_work_bundle_for_planning(
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
        except (WorkOrderValidationError, KeyError, TypeError, ValueError) as exc:
            return no_store_json({"error": str(exc)}, status_code=400)
        return no_store_json(receipt.to_dict())

    async def work_order_artifacts(request: Request) -> Response:
        work_order_id = request.path_params["work_order_id"]
        try:
            result = broker.get_work_order_artifacts(work_order_id)
        except WorkOrderValidationError as exc:
            return no_store_json({"error": str(exc)}, status_code=400)
        return cached_json(request, result)

    return Starlette(
        routes=[
            Route("/", home),
            Route("/healthz", healthz),
            Route("/.well-known/oauth-protected-resource", protected_resource),
            Route("/.well-known/oauth-protected-resource/mcp", protected_resource),
            Route("/v1/planning", submit_planning, methods=["POST"]),
            Route("/v1/planning/{work_order_id}/refresh", refresh_planning, methods=["POST"]),
            Route("/v1/planning/{work_order_id}/plan", get_plan, methods=["GET"]),
            Route("/v1/planning/{work_order_id}/timeline", get_timeline, methods=["GET"]),
            Route(
                "/v1/planning/{work_order_id}/artifacts",
                work_order_artifacts,
                methods=["GET"],
            ),
            Route("/v1/artifacts", begin_artifact, methods=["POST"]),
            Route("/v1/artifacts/{artifact_id}/content", upload_artifact, methods=["PUT"]),
            Route("/v1/artifacts/{artifact_id}/finalize", finalize_artifact, methods=["POST"]),
            Route("/v1/artifacts/{artifact_id}", artifact_status, methods=["GET"]),
            Route("/v1/work-bundles", submit_bundle, methods=["POST"]),
            Mount("/", mcp_app),
        ],
        middleware=[
            Middleware(OAuthResourceMiddleware, settings=resolved, verifier=verifier),
        ],
        lifespan=lifespan,
    )


def _transport_security(settings: Settings) -> Any:
    from mcp.server.transport_security import TransportSecuritySettings

    allowed_hosts: list[str] = []
    for host in settings.allowed_hosts:
        allowed_hosts.append(host)
        if ":" not in host:
            allowed_hosts.append(f"{host}:*")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=settings.is_production,
        allowed_hosts=allowed_hosts,
        allowed_origins=[],
    )


app = None


def asgi_app() -> Starlette:
    global app
    if app is None:
        app = create_app()
    return app
