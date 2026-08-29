from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from oauth_helpers import AUDIENCE, ISSUER, jwt_available, rsa_pair

from awr.auth.hardening import SlidingWindowLimiter
from awr.auth.tokens import AuthError, TokenVerifier
from awr.factory import build_artifact_relay
from awr.observability import _redact_value
from awr.settings import Settings, SettingsError

try:
    import jwt
except ImportError:  # pragma: no cover
    jwt = None  # type: ignore[assignment]

try:
    from starlette.testclient import TestClient

    from awr.executors.recording_cursor import RecordingCursorExecutor
    from awr.factory import build_service
    from awr.storage.firestore import FirestoreStateStore
    from awr.storage.firestore_memory import InMemoryFirestore
    from awr.transports.asgi import create_app
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "env": "test",
        "auth_mode": "static",
        "static_token": "test-token",
        "public_base_url": "http://testserver",
        "allowed_hosts": ("testserver", "localhost", "127.0.0.1"),
        "storage": "memory_firestore",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _auth_header(token: str = "test-token") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@unittest.skipUnless(TestClient is not None, "hosted extra is required")
class McpHardeningTests(unittest.TestCase):
    def _client(self, settings: Settings) -> TestClient:
        store = FirestoreStateStore(InMemoryFirestore())
        service = build_service(store=store, executor=RecordingCursorExecutor())
        return TestClient(create_app(settings=settings, service=service))

    def test_security_headers_on_public_and_protected_routes(self) -> None:
        with self._client(_settings()) as client:
            health = client.get("/healthz")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.headers["x-content-type-options"], "nosniff")
            self.assertEqual(health.headers["x-frame-options"], "DENY")
            self.assertEqual(health.headers["referrer-policy"], "no-referrer")
            self.assertIn("default-src 'none'", health.headers["content-security-policy"])
            self.assertNotIn("strict-transport-security", health.headers)

            denied = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
            self.assertEqual(denied.status_code, 401)
            self.assertEqual(denied.headers["x-content-type-options"], "nosniff")

    def test_production_adds_hsts(self) -> None:
        settings = _settings(
            env="production",
            auth_mode="oauth",
            public_base_url="https://awr.example.test",
            oauth_issuer=ISSUER,
            oauth_audience=AUDIENCE,
            static_token=None,
            storage="firestore",
        )
        with self._client(settings) as client:
            health = client.get("/healthz")
            self.assertEqual(
                health.headers["strict-transport-security"],
                "max-age=31536000; includeSubDomains",
            )

    def test_oversized_json_is_rejected(self) -> None:
        with self._client(_settings(json_body_max_bytes=64)) as client:
            response = client.post(
                "/mcp",
                content=b'{"jsonrpc":"2.0","id":1,"method":"' + (b"a" * 80) + b'"}',
                headers={**_auth_header(), "content-type": "application/json"},
            )
            self.assertEqual(response.status_code, 413)
            self.assertEqual(response.json()["error"], "payload_too_large")

    def test_failed_auth_is_rate_limited(self) -> None:
        settings = _settings(rate_limit_anonymous_per_minute=2, rate_limit_window_seconds=60)
        with self._client(settings) as client:
            first = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
            second = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "ping"})
            third = client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "ping"})
            self.assertEqual(first.status_code, 401)
            self.assertEqual(second.status_code, 401)
            self.assertEqual(third.status_code, 429)
            self.assertEqual(third.json()["error"], "rate_limited")
            self.assertIn("retry-after", third.headers)

    def test_plan_tool_rate_limit(self) -> None:
        settings = _settings(rate_limit_plan_per_minute=1, rate_limit_window_seconds=60)
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "submit_prompt_for_planning",
                "arguments": {
                    "markdown": "@awr feature.plan\n\n# Plan\n",
                    "sender": "chatgpt:product-planner",
                    "recipient": "cursor:recording",
                    "repository_url": "https://github.com/example/project",
                    "idempotency_key": "rate-1",
                },
            },
        }
        headers = {
            **_auth_header(),
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        }
        with self._client(settings) as client:
            first = client.post("/mcp", json=payload, headers=headers)
            self.assertEqual(first.status_code, 200, first.text)
            payload["id"] = 2
            payload["params"]["arguments"]["idempotency_key"] = "rate-2"
            second = client.post("/mcp", json=payload, headers=headers)
            self.assertEqual(second.status_code, 429)
            self.assertEqual(second.json()["error"], "rate_limited")


class HardeningUnitTests(unittest.TestCase):
    def test_sliding_window_blocks_and_recovers(self) -> None:
        limiter = SlidingWindowLimiter()
        self.assertEqual(limiter.allow("k", limit=1, window_seconds=10, now=100.0), 0)
        retry = limiter.allow("k", limit=1, window_seconds=10, now=100.2)
        self.assertGreaterEqual(retry, 1)
        self.assertEqual(limiter.allow("k", limit=1, window_seconds=10, now=111.0), 0)

    def test_production_without_gcs_disables_local_artifacts(self) -> None:
        env = {"AWR_ENV": "production", "AWR_ARTIFACT_STORAGE": "local"}
        with patch.dict(os.environ, env, clear=False):
            self.assertIsNone(build_artifact_relay())

    def test_gcs_flag_does_not_enable_local_disk(self) -> None:
        for env_name in ("production", "local"):
            env = {"AWR_ENV": env_name, "AWR_ARTIFACT_STORAGE": "gcs"}
            with self.subTest(env=env_name):
                with patch.dict(os.environ, env, clear=False):
                    with self.assertRaises(SettingsError) as caught:
                        build_artifact_relay()
                    self.assertIn("not implemented", str(caught.exception))

    def test_logs_redact_upload_tokens(self) -> None:
        self.assertEqual(_redact_value("upload_token", "ticket-secret"), "[REDACTED]")
        self.assertEqual(_redact_value("signed_url", "https://example/sig"), "[REDACTED]")


@unittest.skipUnless(jwt_available(), "JWT extra is required")
class JwtAlgorithmTests(unittest.TestCase):
    def test_hmac_and_none_are_rejected(self) -> None:
        _private_key, jwks = rsa_pair()
        settings = Settings(
            env="test",
            auth_mode="oauth",
            public_base_url="https://awr.example.test",
            oauth_issuer=ISSUER,
            oauth_audience=AUDIENCE,
            extra_jwks=jwks,
        )
        verifier = TokenVerifier(settings)
        assert jwt is not None
        hs = jwt.encode(
            {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "sub": "planner-1",
                "exp": 4_000_000_000,
                "scope": "awr:read",
            },
            "not-an-rsa-key-and-long-enough!!",
            algorithm="HS256",
        )
        with self.assertRaises(AuthError) as caught:
            verifier.verify(hs)
        self.assertIn("algorithm", caught.exception.description.lower())


if __name__ == "__main__":
    unittest.main()
