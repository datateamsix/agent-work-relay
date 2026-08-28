from __future__ import annotations

import json

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..observability import log_event
from ..settings import TOOL_SCOPES, Settings
from .tokens import AuthError, Principal, TokenVerifier, require_scope

PUBLIC_PATHS = {
    "/",
    "/healthz",
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
}

REST_SCOPES = {
    ("POST", "/v1/planning"): "awr:plan",
}


class OAuthResourceMiddleware:
    """OAuth 2.1 resource-server gate for /mcp and state-bearing REST routes."""

    def __init__(self, app: ASGIApp, settings: Settings, verifier: TokenVerifier) -> None:
        self.app = app
        self.settings = settings
        self.verifier = verifier

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        method = scope.get("method", "GET")
        if path in PUBLIC_PATHS or path.startswith("/.well-known/"):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        authorization = headers.get("authorization")
        body, replay = await _buffer_body(receive)
        try:
            principal = self._authenticate(authorization)
            required = self._required_scope(method, path, body)
            require_scope(principal, required)
        except AuthError as exc:
            await _send_auth_error(send, self.settings, exc)
            return

        scope["awr_principal"] = principal
        await self.app(scope, replay, send)

    def _authenticate(self, authorization: str | None) -> Principal:
        if not authorization:
            raise AuthError(401, "invalid_token", "Authentication required.")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise AuthError(401, "invalid_token", "Authentication required.")
        return self.verifier.verify(token.strip())

    @staticmethod
    def _required_scope(method: str, path: str, body: bytes) -> str | None:
        mapped = REST_SCOPES.get((method.upper(), path))
        if mapped:
            return mapped
        if (
            method.upper() == "POST"
            and path.startswith("/v1/planning/")
            and path.endswith("/refresh")
        ):
            return "awr:refresh"
        if method.upper() == "GET" and path.startswith("/v1/planning/"):
            return "awr:read"
        if path != "/mcp":
            return None
        if method.upper() != "POST" or not body:
            return None
        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("method") != "tools/call":
            return None
        params = payload.get("params")
        if not isinstance(params, dict):
            return None
        name = params.get("name")
        if not isinstance(name, str):
            return None
        return TOOL_SCOPES.get(name, "awr:read")


async def _buffer_body(receive: Receive) -> tuple[bytes, Receive]:
    chunks: list[bytes] = []
    more = True
    while more:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunks.append(message.get("body", b""))
        more = bool(message.get("more_body", False))
    body = b"".join(chunks)

    sent = False

    async def replay() -> Message:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return body, replay


async def _send_auth_error(send: Send, settings: Settings, error: AuthError) -> None:
    parts = [
        "Bearer",
        f'resource_metadata="{settings.resource_metadata_url}"',
        f'error="{error.error}"',
        f'error_description="{error.description}"',
    ]
    if error.scope:
        parts.append(f'scope="{error.scope}"')
    header = ", ".join(parts[1:])
    www_authenticate = f"{parts[0]} {header}"
    payload = json.dumps({"error": error.error, "error_description": error.description}).encode(
        "utf-8"
    )
    log_event(
        __import__("logging").getLogger("awr"),
        "auth.challenge",
        status=error.status_code,
        error=error.error,
    )
    await send(
        {
            "type": "http.response.start",
            "status": error.status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode("ascii")),
                (b"www-authenticate", www_authenticate.encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})
