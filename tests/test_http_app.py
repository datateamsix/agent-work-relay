from __future__ import annotations

import json
import time
import unittest

from oauth_helpers import AUDIENCE, ISSUER, rsa_pair, signed_token

from awr.executors.recording_cursor import RecordingCursorExecutor
from awr.factory import build_service
from awr.settings import Settings
from awr.storage.firestore import FirestoreStateStore
from awr.storage.firestore_memory import InMemoryFirestore

try:
    from starlette.testclient import TestClient

    from awr.transports.asgi import create_app
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]


PROMPT = """@awr feature.plan

# Add project health endpoint

Produce an implementation plan. Do not edit files.
"""


def _settings() -> Settings:
    return Settings(
        env="test",
        auth_mode="static",
        static_token="test-token",
        public_base_url="http://testserver",
        allowed_hosts=("testserver", "localhost", "127.0.0.1"),
        storage="memory_firestore",
    )


def _auth_header(token: str = "test-token") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _mcp(
    method: str, params: dict[str, object] | None = None, rpc_id: int = 1
) -> dict[str, object]:
    payload: dict[str, object] = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params is not None:
        payload["params"] = params
    return payload


@unittest.skipUnless(TestClient is not None, "hosted extra is required")
class HostedHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        store = FirestoreStateStore(InMemoryFirestore())
        service = build_service(store=store, executor=RecordingCursorExecutor())
        app = create_app(settings=_settings(), service=service)
        self._context = TestClient(app)
        self.client = self._context.__enter__()

    def tearDown(self) -> None:
        self._context.__exit__(None, None, None)

    def test_healthz_is_public(self) -> None:
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertNotIn("cursor", response.text.lower())

    def test_protected_resource_metadata(self) -> None:
        response = self.client.get("/.well-known/oauth-protected-resource/mcp")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["resource"], "http://testserver/mcp")
        self.assertEqual(
            body["scopes_supported"],
            ["awr:plan", "awr:read", "awr:refresh", "awr:response", "awr:decide"],
        )
        self.assertEqual(body["bearer_methods_supported"], ["header"])

    def test_mcp_missing_token(self) -> None:
        response = self.client.post("/mcp", json=_mcp("ping"))
        self.assertEqual(response.status_code, 401)
        self.assertIn("Bearer", response.headers["www-authenticate"])
        self.assertIn("resource_metadata=", response.headers["www-authenticate"])
        self.assertIn("invalid_token", response.headers["www-authenticate"])

    def test_mcp_invalid_token(self) -> None:
        response = self.client.post("/mcp", json=_mcp("ping"), headers=_auth_header("nope"))
        self.assertEqual(response.status_code, 401)
        self.assertIn("invalid_token", response.headers["www-authenticate"])

    def test_tools_list_and_plan_flow(self) -> None:
        listed = self._rpc("tools/list", {})
        names = sorted(tool["name"] for tool in listed["result"]["tools"])
        self.assertEqual(
            names,
            [
                "begin_artifact_intake",
                "finalize_artifact_upload",
                "get_artifact_status",
                "get_plan",
                "get_work_order",
                "get_work_order_artifacts",
                "get_work_order_timeline",
                "list_pending_actions",
                "record_decision",
                "refresh_planning",
                "submit_prompt_for_planning",
                "submit_response",
                "submit_work_bundle_for_planning",
            ],
        )
        submit = self._rpc(
            "tools/call",
            {
                "name": "submit_prompt_for_planning",
                "arguments": {
                    "markdown": PROMPT,
                    "sender": "chatgpt:product-planner",
                    "recipient": "cursor:recording",
                    "repository_url": "https://github.com/example/project",
                    "idempotency_key": "http-gt-001",
                },
            },
        )
        receipt = self._tool_payload(submit)
        self.assertFalse(receipt["duplicate"])
        self.assertEqual(receipt["status"], "PLANNING")
        work_order_id = receipt["work_order_id"]

        replay = self._rpc(
            "tools/call",
            {
                "name": "submit_prompt_for_planning",
                "arguments": {
                    "markdown": PROMPT,
                    "sender": "chatgpt:product-planner",
                    "recipient": "cursor:recording",
                    "repository_url": "https://github.com/example/project",
                    "idempotency_key": "http-gt-001",
                },
            },
            rpc_id=3,
        )
        replay_receipt = self._tool_payload(replay)
        self.assertTrue(replay_receipt["duplicate"])
        self.assertEqual(replay_receipt["work_order_id"], work_order_id)

        refreshed = self._rpc(
            "tools/call",
            {"name": "refresh_planning", "arguments": {"work_order_id": work_order_id}},
            rpc_id=4,
        )
        plan = self._tool_payload(refreshed)
        self.assertIn("content_sha256", plan)

        timeline = self._rpc(
            "tools/call",
            {"name": "get_work_order_timeline", "arguments": {"work_order_id": work_order_id}},
            rpc_id=5,
        )
        events = self._tool_payload(timeline)
        self.assertEqual(
            [entry["event_type"] for entry in events],
            [
                "work_order.accepted",
                "work_order.routed",
                "executor.acknowledged",
                "plan.received",
                "plan.available",
            ],
        )

    def _rpc(self, method: str, params: dict[str, object], rpc_id: int = 1) -> dict[str, object]:
        response = self.client.post(
            "/mcp",
            json=_mcp(method, params, rpc_id),
            headers={
                **_auth_header(),
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "MCP-Protocol-Version": "2025-06-18",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIsInstance(payload, dict)
        return payload

    @staticmethod
    def _tool_payload(message: dict[str, object]) -> object:
        result = message["result"]
        assert isinstance(result, dict)
        if "structuredContent" in result and result["structuredContent"] is not None:
            structured = result["structuredContent"]
            if isinstance(structured, dict) and "result" in structured:
                return structured["result"]
            return structured
        content = result["content"]
        assert isinstance(content, list)
        first = content[0]
        assert isinstance(first, dict)
        return json.loads(str(first["text"]))


@unittest.skipUnless(TestClient is not None, "hosted extra is required")
class JwtHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.private_key, jwks = rsa_pair()
        settings = Settings(
            env="test",
            auth_mode="oauth",
            public_base_url="http://testserver",
            oauth_issuer=ISSUER,
            oauth_audience=AUDIENCE,
            extra_jwks=jwks,
            allowed_hosts=("testserver", "localhost", "127.0.0.1"),
        )
        store = FirestoreStateStore(InMemoryFirestore())
        service = build_service(store=store, executor=RecordingCursorExecutor())
        app = create_app(settings=settings, service=service)
        self._context = TestClient(app)
        self.client = self._context.__enter__()

    def tearDown(self) -> None:
        self._context.__exit__(None, None, None)

    def test_expired_and_wrong_claims_are_unauthorized(self) -> None:
        expired = signed_token(self.private_key, exp=int(time.time()) - 10)
        response = self.client.post("/mcp", json=_mcp("ping"), headers=_auth_header(expired))
        self.assertEqual(response.status_code, 401)
        self.assertIn("invalid_token", response.headers["www-authenticate"])

        wrong_iss = signed_token(self.private_key, iss="https://evil.example.test")
        response = self.client.post("/mcp", json=_mcp("ping"), headers=_auth_header(wrong_iss))
        self.assertEqual(response.status_code, 401)
        self.assertIn("issuer", response.json()["error_description"].lower())

        wrong_aud = signed_token(self.private_key, aud="https://other.example.test")
        response = self.client.post("/mcp", json=_mcp("ping"), headers=_auth_header(wrong_aud))
        self.assertEqual(response.status_code, 401)
        self.assertIn("audience", response.json()["error_description"].lower())

    def test_insufficient_scope_is_forbidden(self) -> None:
        token = signed_token(self.private_key, scope="awr:read")
        response = self.client.post(
            "/mcp",
            json=_mcp(
                "tools/call",
                {
                    "name": "submit_prompt_for_planning",
                    "arguments": {
                        "markdown": PROMPT,
                        "sender": "chatgpt:product-planner",
                        "recipient": "cursor:recording",
                    },
                },
            ),
            headers=_auth_header(token),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "insufficient_scope")
        self.assertIn("insufficient_scope", response.headers["www-authenticate"])
        self.assertIn("awr:plan", response.headers["www-authenticate"])


@unittest.skipUnless(TestClient is not None, "hosted extra is required")
class HttpCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        store = FirestoreStateStore(InMemoryFirestore())
        service = build_service(store=store, executor=RecordingCursorExecutor())
        app = create_app(settings=_settings(), service=service)
        self._context = TestClient(app)
        self.client = self._context.__enter__()

    def tearDown(self) -> None:
        self._context.__exit__(None, None, None)

    def test_plan_read_uses_etag_and_mutations_are_no_store(self) -> None:
        created = self.client.post(
            "/v1/planning",
            json={
                "markdown": PROMPT,
                "sender": "chatgpt:product-planner",
                "recipient": "cursor:recording",
                "repository_url": "https://github.com/example/project",
                "idempotency_key": "etag-plan",
            },
            headers=_auth_header(),
        )
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.headers["cache-control"], "no-store")
        work_order_id = created.json()["work_order_id"]

        refreshed = self.client.post(
            f"/v1/planning/{work_order_id}/refresh",
            headers=_auth_header(),
        )
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(refreshed.headers["cache-control"], "no-store")

        first = self.client.get(
            f"/v1/planning/{work_order_id}/plan",
            headers=_auth_header(),
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.headers["cache-control"], "private, no-cache")
        etag = first.headers["etag"]
        self.assertTrue(etag.startswith('"sha256:'))

        cached = self.client.get(
            f"/v1/planning/{work_order_id}/plan",
            headers={**_auth_header(), "If-None-Match": etag},
        )
        self.assertEqual(cached.status_code, 304)
        self.assertEqual(cached.headers["etag"], etag)
        self.assertEqual(cached.headers["cache-control"], "private, no-cache")


if __name__ == "__main__":
    unittest.main()
