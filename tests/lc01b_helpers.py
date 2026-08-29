from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from awr.contracts import WorkStatus
from awr.executors.recording_cursor import RecordingCursorExecutor
from awr.artifacts.contracts import ArtifactPurpose, ArtifactReference
from awr.responses.canonical import fingerprint_packet
from awr.responses.contracts import RESPONSE_AUTHORITY, RESPONSE_SCHEMA, ResponsePacket, ResponseType
from awr.responses.render import render_response_markdown
from awr.responses.validate import parse_response_markdown
from awr.service import BrokerService
from awr.storage.sqlite import SQLiteStateStore

FEATURE = """@awr feature.plan

# Add a health endpoint

Produce an implementation plan. Do not edit files.
"""
REPOSITORY = "https://github.com/example/project"
SENDER = "chatgpt:product-planner"
RECIPIENT = "cursor:worker"
PLAN_BODY = "1. Inspect the router.\n2. Add GET /healthz.\n3. Cover it with a test."


class LifecycleHarness:
    def __init__(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.store = SQLiteStateStore(root / "awr.db")
        self.service = BrokerService(self.store, RecordingCursorExecutor())
        self.clock = 0

    def close(self) -> None:
        self.temp_dir.cleanup()

    def accept_planning(self, *, key: str = "lc01b-plan") -> str:
        receipt = self.service.submit_prompt_for_planning(
            markdown=FEATURE,
            sender=SENDER,
            recipient=RECIPIENT,
            repository_url=REPOSITORY,
            idempotency_key=key,
        )
        assert receipt.status is WorkStatus.PLANNING
        return receipt.work_order_id

    def projection(self, work_order_id: str) -> dict[str, object]:
        return self.service.get_work_order(work_order_id, actor=SENDER)

    def parent_and_source(self, work_order_id: str) -> tuple[str, str]:
        payload = self.projection(work_order_id)
        lifecycle = payload["lifecycle"]
        assert isinstance(lifecycle, dict)
        return str(lifecycle["current_parent_id"]), str(lifecycle["source_input_sha256"])

    def render_parse(
        self,
        *,
        response_type: ResponseType,
        work_order_id: str,
        payload: dict[str, object],
        actor: str,
        idempotency_key: str,
        executor_run_id: str | None = None,
        evidence_refs: list[dict[str, object]] | None = None,
        authority: str = RESPONSE_AUTHORITY,
        extra_frontmatter: str = "",
        in_reply_to: str | None = None,
        source_input_sha256: str | None = None,
    ) -> tuple[str, ResponsePacket]:
        parent, source = self.parent_and_source(work_order_id)
        parent = in_reply_to or parent
        source = source_input_sha256 or source
        self.clock += 1
        refs = ()
        if evidence_refs:
            refs = tuple(
                ArtifactReference(
                    artifact_id=str(item["artifact_id"]),
                    purpose=item["purpose"]
                    if isinstance(item["purpose"], ArtifactPurpose)
                    else ArtifactPurpose(str(item["purpose"])),
                    byte_length=int(item["byte_length"]),
                    sha256=str(item["sha256"]),
                    detected_media_type=(
                        str(item["detected_media_type"]) if item.get("detected_media_type") else None
                    ),
                    safe_filename=str(item["safe_filename"]),
                )
                for item in evidence_refs
            )
        packet = ResponsePacket(
            schema=RESPONSE_SCHEMA,
            response_type=response_type,
            work_order_id=work_order_id,
            in_reply_to=parent,
            idempotency_key=idempotency_key,
            source_input_sha256=source,
            created_at=f"2026-08-29T00:00:{self.clock:02d}+00:00",
            authority=authority,
            payload=payload,
            evidence_refs=refs,
            message_id=None,
            executor_run_id=executor_run_id,
            actor=actor,
        )
        markdown = render_response_markdown(packet)
        if extra_frontmatter:
            markdown = markdown.replace("  authority:", extra_frontmatter + "  authority:")
        parsed = parse_response_markdown(markdown)
        return markdown, parsed

    def submit(
        self,
        *,
        response_type: ResponseType,
        work_order_id: str,
        payload: dict[str, object],
        actor: str,
        idempotency_key: str,
        executor_run_id: str | None = None,
        evidence_refs: list[dict[str, object]] | None = None,
    ) -> tuple[str, ResponsePacket, dict[str, object]]:
        markdown, parsed = self.render_parse(
            response_type=response_type,
            work_order_id=work_order_id,
            payload=payload,
            actor=actor,
            idempotency_key=idempotency_key,
            executor_run_id=executor_run_id,
            evidence_refs=evidence_refs,
        )
        calculated = fingerprint_packet(parsed)
        if parsed.content_sha256 != calculated:
            raise AssertionError("Declared fingerprint does not match the canonical packet.")
        receipt = self.service.submit_response(markdown=markdown, actor=actor)
        return markdown, parsed, receipt


def plan_payload(content: str = PLAN_BODY, plan_id: str = "PLAN-1") -> dict[str, object]:
    return {
        "content": content,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "title": "Health endpoint plan",
        "plan_id": plan_id,
    }
