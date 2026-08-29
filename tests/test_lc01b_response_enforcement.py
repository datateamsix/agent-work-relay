from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from lc01b_helpers import FEATURE, RECIPIENT, REPOSITORY, SENDER, LifecycleHarness, plan_payload

from awr.artifacts.relay import ArtifactRelay
from awr.artifacts.scan import CleanScanner
from awr.artifacts.security import ArtifactSecurityService
from awr.artifacts.service import ArtifactService
from awr.contracts import WorkStatus
from awr.decorators import DirectiveError
from awr.executors.recording_cursor import RecordingCursorExecutor
from awr.responses.canonical import ResponsePacketError
from awr.responses.contracts import ResponseType
from awr.responses.validate import parse_response_markdown
from awr.service import BrokerService, WorkOrderValidationError
from awr.storage.artifact_fs import LocalArtifactBodyStore
from awr.storage.artifact_sqlite import SQLiteArtifactMetadataStore
from awr.storage.quarantine_only import QuarantineOnlyBodyStore
from awr.storage.sqlite import SQLiteStateStore

FIXTURE = Path(__file__).parent / "fixtures" / "lc01a_completion_as_displayed.md"


class ResponseEnforcementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = LifecycleHarness()
        self.work_order_id = self.harness.accept_planning()

    def tearDown(self) -> None:
        self.harness.close()

    def test_lc01a_displayed_completion_is_rejected(self) -> None:
        markdown = FIXTURE.read_text(encoding="utf-8")
        with self.assertRaises((ResponsePacketError, DirectiveError)):
            parse_response_markdown(markdown)
        with self.assertRaises(WorkOrderValidationError):
            self.harness.service.submit_response(markdown=markdown, actor=RECIPIENT)

    def test_malformed_decorator_is_rejected(self) -> None:
        with self.assertRaises(WorkOrderValidationError):
            self.harness.service.submit_response(
                markdown="# just a plan\n",
                actor=RECIPIENT,
            )

    def test_wrong_type_for_state_is_rejected(self) -> None:
        with self.assertRaisesRegex(WorkOrderValidationError, "not permitted"):
            self.harness.submit(
                response_type=ResponseType.EXECUTION_COMPLETED,
                work_order_id=self.work_order_id,
                payload={"summary": "Too early"},
                actor=RECIPIENT,
                idempotency_key="too-early",
            )

    def test_missing_required_field_is_rejected(self) -> None:
        parent, source = self.harness.parent_and_source(self.work_order_id)
        markdown = (
            "@response\n---\nawr:\n"
            "  schema: awr.response/v1\n"
            "  response_type: plan.completed\n"
            f"  work_order_id: {self.work_order_id}\n"
            f"  in_reply_to: {parent}\n"
            "  idempotency_key: missing-body-hash\n"
            f"  source_input_sha256: sha256:{source}\n"
            "  created_at: 2026-08-29T00:00:00+00:00\n"
            "  authority: report_only\n"
            "---\n\nPlan without a body hash.\n"
        )
        with self.assertRaises(WorkOrderValidationError):
            self.harness.service.submit_response(markdown=markdown, actor=RECIPIENT)

    def test_wrong_parent_and_source_are_rejected(self) -> None:
        markdown, _ = self.harness.render_parse(
            response_type=ResponseType.PLAN_COMPLETED,
            work_order_id=self.work_order_id,
            payload=plan_payload(),
            actor=RECIPIENT,
            idempotency_key="wrong-parent",
            in_reply_to="MSG-someone-else",
        )
        with self.assertRaisesRegex(WorkOrderValidationError, "immediate parent"):
            self.harness.service.submit_response(markdown=markdown, actor=RECIPIENT)
        other, _ = self.harness.render_parse(
            response_type=ResponseType.PLAN_COMPLETED,
            work_order_id=self.work_order_id,
            payload=plan_payload(),
            actor=RECIPIENT,
            idempotency_key="wrong-source",
            source_input_sha256="f" * 64,
        )
        with self.assertRaisesRegex(WorkOrderValidationError, "source_input_sha256"):
            self.harness.service.submit_response(markdown=other, actor=RECIPIENT)

    def test_wrong_actor_is_rejected(self) -> None:
        markdown, _ = self.harness.render_parse(
            response_type=ResponseType.PLAN_COMPLETED,
            work_order_id=self.work_order_id,
            payload=plan_payload(),
            actor=RECIPIENT,
            idempotency_key="wrong-actor",
        )
        with self.assertRaisesRegex(WorkOrderValidationError, "not a work-order participant"):
            self.harness.service.submit_response(markdown=markdown, actor="intruder:agent")

    def test_authority_other_than_report_only_is_rejected(self) -> None:
        with self.assertRaisesRegex(ResponsePacketError, "never grant"):
            self.harness.render_parse(
                response_type=ResponseType.PLAN_COMPLETED,
                work_order_id=self.work_order_id,
                payload=plan_payload(),
                actor=RECIPIENT,
                idempotency_key="authority",
                authority="approve",
            )

    def test_idempotency_rebind_is_rejected(self) -> None:
        markdown, _, first = self.harness.submit(
            response_type=ResponseType.PLAN_COMPLETED,
            work_order_id=self.work_order_id,
            payload=plan_payload(),
            actor=RECIPIENT,
            idempotency_key="same-key",
        )
        self.assertFalse(first["duplicate"])
        rebound, _ = self.harness.render_parse(
            response_type=ResponseType.PLAN_COMPLETED,
            work_order_id=self.work_order_id,
            payload=plan_payload(content="A different plan body."),
            actor=RECIPIENT,
            idempotency_key="same-key",
            in_reply_to=self.work_order_id,
            source_input_sha256=str(
                self.harness.projection(self.work_order_id)["lifecycle"]["source_input_sha256"]
            ),
        )
        with self.assertRaisesRegex(WorkOrderValidationError, "already bound"):
            self.harness.service.submit_response(markdown=rebound, actor=RECIPIENT)
        replay = self.harness.service.submit_response(markdown=markdown, actor=RECIPIENT)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(replay["status"], WorkStatus.PLAN_READY.value)

    def test_wrong_provider_run_is_rejected(self) -> None:
        self.harness.submit(
            response_type=ResponseType.PLAN_COMPLETED,
            work_order_id=self.work_order_id,
            payload=plan_payload(),
            actor=RECIPIENT,
            idempotency_key="plan-for-run",
        )
        self.harness.service.request_plan_approval(self.work_order_id, actor=SENDER)
        lifecycle = self.harness.projection(self.work_order_id)["lifecycle"]
        self.harness.service.record_decision(
            decision_type="approve_plan",
            work_order_id=self.work_order_id,
            actor=SENDER,
            target_id=str(lifecycle["plan_id"]),
            target_sha256=str(lifecycle["plan_sha256"]),
            idempotency_key="approve-run",
            permitted_action="plan.execute",
        )
        self.harness.service.dispatch_execution(
            self.work_order_id,
            actor=SENDER,
            plan_id=str(lifecycle["plan_id"]),
            plan_sha256=str(lifecycle["plan_sha256"]),
        )
        self.harness.submit(
            response_type=ResponseType.EXECUTION_ACKNOWLEDGED,
            work_order_id=self.work_order_id,
            payload={"executor": "cursor:cloud", "executor_run_id": "run-expected"},
            actor=RECIPIENT,
            idempotency_key="ack-expected",
            executor_run_id="run-expected",
        )
        with self.assertRaisesRegex(WorkOrderValidationError, "acknowledged provider run"):
            self.harness.submit(
                response_type=ResponseType.EXECUTION_COMPLETED,
                work_order_id=self.work_order_id,
                payload={"summary": "Wrong run"},
                actor=RECIPIENT,
                idempotency_key="wrong-run",
                executor_run_id="run-other",
            )


class EvidenceEnforcementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        store = SQLiteStateStore(root / "awr.db")
        metadata = SQLiteArtifactMetadataStore(root / "awr.db")
        bodies = LocalArtifactBodyStore(root / "artifacts")
        intake = ArtifactService(metadata, QuarantineOnlyBodyStore(bodies), max_bytes=1024)
        security = ArtifactSecurityService(metadata, bodies, CleanScanner(), max_bytes=1024)
        self.service = BrokerService(
            store,
            RecordingCursorExecutor(),
            artifacts=ArtifactRelay(intake, security, metadata, bodies),
        )
        self.metadata = metadata
        receipt = self.service.submit_prompt_for_planning(
            markdown=FEATURE,
            sender=SENDER,
            recipient=RECIPIENT,
            repository_url=REPOSITORY,
            idempotency_key="evidence-plan",
        )
        self.work_order_id = receipt.work_order_id
        self.harness = LifecycleHarness.__new__(LifecycleHarness)
        self.harness.service = self.service
        self.harness.store = store
        self.harness.clock = 0
        self.harness.temp_dir = self.temp_dir

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _clean_artifact(self, *, owner: str, key: str) -> dict[str, object]:
        begun = self.service.begin_artifact_intake(
            owner=owner,
            original_filename="note.txt",
            declared_media_type="text/plain",
            purpose="other_reference",
            idempotency_key=key,
        )
        payload = b"clean evidence"
        self.service.upload_artifact_content(
            begun["artifact_id"],
            io.BytesIO(payload),
            actor=owner,
            token=str(begun["upload_token"]),
        )
        self.service.finalize_artifact_upload(begun["artifact_id"], actor=owner)
        current = self.metadata.get(str(begun["artifact_id"]))
        assert current is not None
        return {
            "artifact_id": current.artifact_id,
            "purpose": current.purpose.value,
            "byte_length": current.byte_length,
            "sha256": current.sha256,
            "detected_media_type": current.detected_media_type,
            "safe_filename": current.original_filename,
        }

    def test_wrong_owner_evidence_is_rejected(self) -> None:
        reference = self._clean_artifact(owner="other:owner", key="wrong-owner")
        with self.assertRaisesRegex(WorkOrderValidationError, "owner"):
            self.harness.submit(
                response_type=ResponseType.PLAN_COMPLETED,
                work_order_id=self.work_order_id,
                payload=plan_payload(),
                actor=RECIPIENT,
                idempotency_key="evidence-owner",
                evidence_refs=reference if isinstance(reference, list) else [reference],
            )

    def test_unknown_evidence_is_rejected(self) -> None:
        with self.assertRaisesRegex(WorkOrderValidationError, "Unknown evidence"):
            self.harness.submit(
                response_type=ResponseType.PLAN_COMPLETED,
                work_order_id=self.work_order_id,
                payload=plan_payload(),
                actor=RECIPIENT,
                idempotency_key="evidence-missing",
                evidence_refs=[
                    {
                        "artifact_id": "ART-missing",
                        "purpose": "other_reference",
                        "byte_length": 4,
                        "sha256": "a" * 64,
                        "detected_media_type": "text/plain",
                        "safe_filename": "note.txt",
                    }
                ],
            )


if __name__ == "__main__":
    unittest.main()
