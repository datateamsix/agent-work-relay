from __future__ import annotations

from collections import deque
from threading import Lock
from time import monotonic

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..settings import TOOL_SCOPES, Settings

SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"x-permitted-cross-domain-policies", b"none"),
    (b"content-security-policy", b"default-src 'none'; style-src 'unsafe-inline'; img-src data:"),
)

_HSTS = (b"strict-transport-security", b"max-age=31536000; includeSubDomains")


class RateLimited(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("Rate limit exceeded.")
        self.retry_after = retry_after


class SlidingWindowLimiter:
    """In-process sliding window. Cloud Run instances do not share this map."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = Lock()

    def allow(self, key: str, *, limit: int, window_seconds: int, now: float | None = None) -> int:
        if limit <= 0:
            return 0
        stamp = monotonic() if now is None else now
        cutoff = stamp - window_seconds
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                retry = max(1, int(window_seconds - (stamp - bucket[0])) + 1)
                return retry
            bucket.append(stamp)
        return 0


def client_ip(headers: Headers) -> str:
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip() or "unknown"
    return "unknown"


def rate_limit_key(
    *,
    authenticated: bool,
    subject: str,
    method: str,
    path: str,
    tool_name: str | None,
) -> str:
    if not authenticated:
        return f"anon:{subject}"
    if tool_name:
        return f"tool:{subject}:{tool_name}"
    if method.upper() == "POST" and path == "/v1/planning":
        return f"tool:{subject}:submit_prompt_for_planning"
    if method.upper() == "POST" and path == "/v1/work-bundles":
        return f"tool:{subject}:submit_work_bundle_for_planning"
    if method.upper() == "POST" and path.endswith("/refresh-external"):
        return f"tool:{subject}:refresh_external_run"
    if method.upper() == "POST" and path.endswith("/refresh"):
        return f"tool:{subject}:refresh_planning"
    return f"auth:{subject}"


def limit_for_key(key: str, settings: Settings) -> int:
    if key.startswith("anon:"):
        return settings.rate_limit_anonymous_per_minute
    if key.endswith(":refresh_external_run"):
        return settings.rate_limit_execute_per_minute
    if key.endswith((":submit_prompt_for_planning", ":submit_work_bundle_for_planning")):
        return settings.rate_limit_plan_per_minute
    if key.startswith("tool:"):
        tool = key.rsplit(":", 1)[-1]
        scope = TOOL_SCOPES.get(tool)
        if scope == "awr:execute":
            return settings.rate_limit_execute_per_minute
        if scope in {"awr:plan", "awr:decide", "awr:response"}:
            return settings.rate_limit_plan_per_minute
    return settings.rate_limit_authenticated_per_minute


class SecurityHeadersMiddleware:
    """Apply browser isolation headers. TLS is terminated at Cloud Run."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                existing = {key.lower() for key, _ in headers}
                for key, value in SECURITY_HEADERS:
                    if key not in existing:
                        headers.append((key, value))
                if self.settings.is_production and _HSTS[0] not in existing:
                    headers.append(_HSTS)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
