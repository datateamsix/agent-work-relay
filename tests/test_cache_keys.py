from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from awr.executors.recording_cursor import RecordingCursorExecutor
from awr.factory import build_artifact_relay
from awr.responses.cache import (
    etag_for_digest,
    provider_run_cache_key,
    replay_cache_key,
    response_idempotency_cache_key,
    response_packet_cache_key,
    security_receipt_cache_key,
)
from awr.responses.canonical import canonical_json, fingerprint_bytes
from awr.responses.contracts import POLICY_VERSION
from awr.service import BrokerService
from awr.storage.sqlite import SQLiteStateStore

FEATURE = """@awr feature.plan

# Feature
"""
REPOSITORY = "https://github.com/example/project"


class CacheKeyContractTests(unittest.TestCase):
    def test_replay_key_matches_broker_idempotency(self) -> None:
        temp = tempfile.TemporaryDirectory()
        try:
            root = Path(temp.name)
            service = BrokerService(
                SQLiteStateStore(root / "awr.db"),
                RecordingCursorExecutor(),
                artifacts=build_artifact_relay(root / "awr.db", root / "artifacts"),
            )
            receipt = service.submit_prompt_for_planning(
                markdown=FEATURE,
                sender="chatgpt:planner",
                recipient="cursor:backend",
                repository_url=REPOSITORY,
                base_ref="main",
            )
            stored = service.store.get_work_order(receipt.work_order_id)
            assert stored is not None
            expected = replay_cache_key(
                sender="chatgpt:planner",
                recipient="cursor:backend",
                directive="feature.plan",
                parent=None,
                repository_url=REPOSITORY,
                base_ref="main",
                content_sha256=stored.content_sha256,
            )
            self.assertEqual(stored.idempotency_key, expected)
        finally:
            temp.cleanup()

    def test_security_receipt_key_includes_scanner_and_policy_versions(self) -> None:
        digest = "a" * 64
        left = security_receipt_cache_key(
            sha256=digest,
            scanner_id="fake-clean",
            scanner_version="1",
            signature_version="1",
            policy_version=POLICY_VERSION,
        )
        same = security_receipt_cache_key(
            sha256=digest,
            scanner_id="fake-clean",
            scanner_version="1",
            signature_version="1",
        )
        changed = security_receipt_cache_key(
            sha256=digest,
            scanner_id="fake-clean",
            scanner_version="2",
            signature_version="1",
        )
        self.assertEqual(left, same)
        self.assertNotEqual(left, changed)

    def test_response_and_etag_keys_are_stable(self) -> None:
        first = response_packet_cache_key(canonical_sha256="b" * 64)
        second = response_packet_cache_key(canonical_sha256="b" * 64)
        other = response_packet_cache_key(canonical_sha256="b" * 64, template_version="9.0.0")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertEqual(etag_for_digest("c" * 64), f'"sha256:{"c" * 64}"')
        first_id = response_idempotency_cache_key(
            actor="cursor:worker",
            operation="plan.completed",
            idempotency_key="k1",
            packet_fingerprint="d" * 64,
        )
        same_id = response_idempotency_cache_key(
            actor="cursor:worker",
            operation="plan.completed",
            idempotency_key="k1",
            packet_fingerprint="sha256:" + "d" * 64,
        )
        other_id = response_idempotency_cache_key(
            actor="cursor:worker",
            operation="plan.completed",
            idempotency_key="k1",
            packet_fingerprint="e" * 64,
        )
        self.assertEqual(first_id, same_id)
        self.assertNotEqual(first_id, other_id)
        self.assertNotEqual(
            provider_run_cache_key(
                provider="cursor",
                agent_id="agent-1",
                run_id="run-1",
                last_known_version="1",
            ),
            provider_run_cache_key(
                provider="cursor",
                agent_id="agent-1",
                run_id="run-2",
                last_known_version="1",
            ),
        )

    def test_cached_json_fingerprints_before_optional_gzip(self) -> None:
        try:
            from starlette.requests import Request

            from awr.transports.http_cache import cached_json, no_store_json
        except ImportError:
            self.skipTest("hosted extra is required")
        payload = {"note": "x" * 600}
        encoded = canonical_json(payload)
        digest = fingerprint_bytes(encoded)
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/v1/planning/AWR-1/plan",
                "headers": [(b"accept-encoding", b"gzip")],
            }
        )
        response = cached_json(request, payload)
        self.assertEqual(response.headers["etag"], etag_for_digest(digest))
        self.assertEqual(response.headers["content-encoding"], "gzip")
        self.assertEqual(response.headers["cache-control"], "private, no-cache")
        self.assertGreater(len(encoded), 512)
        self.assertLess(len(response.body), len(encoded))
        not_modified = cached_json(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/v1/planning/AWR-1/plan",
                    "headers": [(b"if-none-match", response.headers["etag"].encode("ascii"))],
                }
            ),
            payload,
        )
        self.assertEqual(not_modified.status_code, 304)
        mutation = no_store_json({"token": "secret"})
        self.assertEqual(mutation.headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()
