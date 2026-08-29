from __future__ import annotations

import json
import logging

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..observability import log_event
from ..settings import TOOL_SCOPES, Settings
from .context import reset_current_principal, set_current_principal
from .hardening import (
    RateLimited,
    SlidingWindowLimiter,
    client_ip,
    limit_for_key,
    rate_limit_key,
)
from .tokens import AuthError, Principal, TokenVerifier, require_scope

PUBLIC_PATHS = {
    "/",
    "/healthz",
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
}

REST_SCOPES = {
    ("POST", "/v1/planning"): "awr:plan",
    ("POST", "/v1/artifacts"): "awr:plan",
    ("POST", "/v1/work-bundles"): "awr:plan",
}


class OAuthResourceMiddleware:
    """OAuth 2.1 resource-server gate for /mcp and state-bearing REST routes."""

    def __init__(
        self,
        app: ASGIApp,
        settings: Settings,
        verifier: TokenVerifier,
        limiter: SlidingWindowLimiter | None = None,
    ) -> None:
        self.app = app
        self.settings = settings
        self.verifier = verifier
        self.limiter = limiter or SlidingWindowLimiter()

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
        peer = client_ip(headers)
        if _is_artifact_upload(method, path):
            try:
                principal = self._authenticate_or_limit(authorization, peer, method, path)
                require_scope(principal, "awr:plan")
                self._rate_limit(
                    authenticated=True,
                    subject=principal.subject,
                    method=method,
                    path=path,
                    tool_name="begin_artifact_intake",
                )
            except RateLimited as exc:
                await _send_rate_limit(send, exc)
                return
            except AuthError as exc:
                await _send_auth_error(send, self.settings, exc)
                return
            token = set_current_principal(principal)
            scope["awr_principal"] = principal
            try:
                await self.app(scope, receive, send)
            finally:
                reset_current_principal(token)
            return

        try:
            body, replay = await _buffer_body(receive, max_bytes=self.settings.json_body_max_bytes)
        except _BodyTooLarge:
            await _send_payload_too_large(send, self.settings.json_body_max_bytes)
            return
        tool_name = _tool_name(method, path, body)
        try:
            principal = self._authenticate_or_limit(
                authorization, peer, method, path, tool_name=tool_name
            )
            required = self._required_scope(method, path, body)
            require_scope(principal, required)
            self._rate_limit(
                authenticated=True,
                subject=principal.subject,
                method=method,
                path=path,
                tool_name=tool_name,
            )
        except RateLimited as exc:
            await _send_rate_limit(send, exc)
            return
        except AuthError as exc:
            await _send_auth_error(send, self.settings, exc)
            return

        if tool_name:
            log_event(
                logging.getLogger("awr"),
                "mcp.tool_call",
                tool=tool_name,
                actor=principal.subject,
                client_id=principal.client_id,
                scope=required,
                path=path,
            )
        token = set_current_principal(principal)
        scope["awr_principal"] = principal
        try:
            await self.app(scope, replay, send)
        finally:
            reset_current_principal(token)

    def _rate_limit(
        self,
        *,
        authenticated: bool,
        subject: str,
        method: str,
        path: str,
        tool_name: str | None = None,
    ) -> None:
        key = rate_limit_key(
            authenticated=authenticated,
            subject=subject,
            method=method,
            path=path,
            tool_name=tool_name,
        )
        retry = self.limiter.allow(
            key,
            limit=limit_for_key(key, self.settings),
            window_seconds=self.settings.rate_limit_window_seconds,
        )
        if retry:
            raise RateLimited(retry)

    def _authenticate_or_limit(
        self,
        authorization: str | None,
        peer: str,
        method: str,
        path: str,
        tool_name: str | None = None,
    ) -> Principal:
        try:
            return self._authenticate(authorization)
        except AuthError:
            self._rate_limit(
                authenticated=False,
                subject=peer,
                method=method,
                path=path,
                tool_name=tool_name,
            )
            raise

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
        if (
            method.upper() == "POST"
            and path.startswith("/v1/work-orders/")
            and path.endswith("/refresh-external")
        ):
            return "awr:execute"
        if method.upper() == "GET" and path.startswith("/v1/planning/"):
            return "awr:read"
        if method.upper() == "GET" and path.startswith("/v1/artifacts/"):
            return "awr:read"
        if (
            method.upper() == "POST"
            and path.startswith("/v1/artifacts/")
            and path.endswith("/finalize")
        ):
            return "awr:plan"
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


class _BodyTooLarge(Exception):
    """JSON or MCP request exceeded the configured body cap."""


def _is_artifact_upload(method: str, path: str) -> bool:
    return (
        method.upper() == "PUT" and path.startswith("/v1/artifacts/") and path.endswith("/content")
    )


def _tool_name(method: str, path: str, body: bytes) -> str | None:
    if method.upper() != "POST" or path != "/mcp" or not body:
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
    return name if isinstance(name, str) and name else None


async def _buffer_body(receive: Receive, *, max_bytes: int) -> tuple[bytes, Receive]:
    chunks: list[bytes] = []
    total = 0
    more = True
    while more:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunk = message.get("body", b"")
        total += len(chunk)
        if total > max_bytes:
            raise _BodyTooLarge
        chunks.append(chunk)
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
        logging.getLogger("awr"),
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


async def _send_rate_limit(send: Send, error: RateLimited) -> None:
    payload = json.dumps(
        {"error": "rate_limited", "error_description": "Rate limit exceeded."}
    ).encode("utf-8")
    log_event(
        logging.getLogger("awr"),
        "auth.rate_limited",
        retry_after=error.retry_after,
    )
    await send(
        {
            "type": "http.response.start",
            "status": 429,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode("ascii")),
                (b"retry-after", str(error.retry_after).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


async def _send_payload_too_large(send: Send, max_bytes: int) -> None:
    payload = json.dumps(
        {
            "error": "payload_too_large",
            "error_description": f"JSON body exceeds the {max_bytes} byte limit.",
        }
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})
