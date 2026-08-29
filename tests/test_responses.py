from __future__ import annotations

import unittest

from awr.artifacts.contracts import ArtifactPurpose, ArtifactReference
from awr.decorators import DirectiveError
from awr.responses import (
    RESPONSE_AUTHORITY,
    RESPONSE_SCHEMA,
    ResponsePacketError,
    ResponseType,
    parse_response_markdown,
    parse_response_packet,
    render_response_markdown,
    response_packet_cache_key,
)
from awr.responses.canonical import fingerprint_packet


def _base(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": RESPONSE_SCHEMA,
        "response_type": "plan.completed",
        "work_order_id": "AWR-1",
        "in_reply_to": "msg-1",
        "idempotency_key": "resp-1",
        "source_input_sha256": "a" * 64,
        "created_at": "2026-08-29T00:00:00+00:00",
        "authority": RESPONSE_AUTHORITY,
        "payload": {"content": "Inspect the code.", "content_sha256": "b" * 64},
    }
    payload.update(overrides)
    return payload


class ResponseContractTests(unittest.TestCase):
    def test_all_discriminators_parse(self) -> None:
        cases = {
            "receipt.accepted": {
                "receipt_type": "work_order.accepted",
                "status": "PLANNING",
                "content_sha256": "c" * 64,
            },
            "plan.completed": {"content": "Plan", "content_sha256": "d" * 64},
            "question.blocked": {"questions": [{"id": "q1", "text": "Which API?"}]},
            "execution.acknowledged": {"executor": "cursor:cloud", "executor_run_id": "run-1"},
            "execution.progress": {"message": "Halfway", "percent": 50},
            "execution.completed": {"summary": "Done"},
            "execution.failed": {"error_type": "Timeout", "message": "Cursor expired"},
            "review.completed": {"outcome": "APPROVED", "rationale": "Looks good"},
        }
        for response_type, body in cases.items():
            packet = parse_response_packet(_base(response_type=response_type, payload=body))
            self.assertEqual(packet.response_type, ResponseType(response_type))
            self.assertEqual(packet.authority, RESPONSE_AUTHORITY)
            self.assertEqual(len(packet.content_sha256 or ""), 64)

    def test_unknown_field_and_authority_fail_closed(self) -> None:
        with self.assertRaisesRegex(ResponsePacketError, "Unknown response fields"):
            parse_response_packet(_base(extra="nope"))
        with self.assertRaisesRegex(ResponsePacketError, "never grant"):
            parse_response_packet(_base(authority="execution"))
        with self.assertRaisesRegex(ResponsePacketError, "Unknown response_type"):
            parse_response_packet(_base(response_type="plan.execute"))

    def test_inline_logs_must_be_artifact_refs(self) -> None:
        with self.assertRaisesRegex(ResponsePacketError, "artifact references"):
            parse_response_packet(
                _base(payload={"content": "x", "content_sha256": "e" * 64, "diff": "---"})
            )
        reference = ArtifactReference(
            artifact_id="ART-1",
            purpose=ArtifactPurpose.OTHER_REFERENCE,
            byte_length=4,
            sha256="f" * 64,
            detected_media_type="text/plain",
            safe_filename="log.txt",
        )
        packet = parse_response_packet(_base(evidence_refs=[reference.to_dict()]))
        self.assertEqual(packet.evidence_refs[0].artifact_id, "ART-1")

    def test_render_round_trip_and_cache_key(self) -> None:
        packet = parse_response_packet(_base())
        markdown = render_response_markdown(packet)
        self.assertTrue(markdown.startswith("@response"))
        rendered = parse_response_markdown(markdown)
        self.assertEqual(rendered.response_type, ResponseType.PLAN_COMPLETED)
        self.assertEqual(
            response_packet_cache_key(canonical_sha256=fingerprint_packet(packet)),
            response_packet_cache_key(canonical_sha256=packet.content_sha256 or ""),
        )

    def test_execution_completed_render_round_trip(self) -> None:
        packet = parse_response_packet(
            _base(response_type="execution.completed", payload={"summary": "Done"})
        )
        markdown = render_response_markdown(packet)
        rendered = parse_response_markdown(markdown)
        self.assertEqual(rendered.response_type, ResponseType.EXECUTION_COMPLETED)
        self.assertEqual(rendered.payload["summary"], "Done")
        self.assertEqual(rendered.content_sha256, fingerprint_packet(rendered))
        self.assertEqual(rendered.content_sha256, packet.content_sha256)

    def test_response_markdown_requires_decorator(self) -> None:
        with self.assertRaises(DirectiveError):
            parse_response_markdown("# just a plan")


if __name__ == "__main__":
    unittest.main()
