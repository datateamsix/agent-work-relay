from __future__ import annotations

import hashlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from artifact_fixtures import (
    ACTIVE_PDF,
    EICAR,
    ENCRYPTED_PDF,
    png_bomb_header,
    png_bytes,
    truncated_png,
)

from awr.artifacts.contracts import (
    CONTROL_AUTHORITY_PRIMARY_MARKDOWN,
    ArtifactPurpose,
    ArtifactSecurityVerdict,
    ArtifactStatus,
)
from awr.artifacts.errors import ArtifactAccessError, ArtifactError
from awr.artifacts.scan import (
    ClamAvScanner,
    EicarScanner,
    MalformedScanner,
    ScanOutcome,
    TimeoutScanner,
    UnavailableScanner,
)
from awr.artifacts.security import ArtifactSecurityService
from awr.artifacts.service import ArtifactService
from awr.artifacts.validate import PILLOW_AVAILABLE, PYPDF_AVAILABLE, PYYAML_AVAILABLE
from awr.factory import build_artifact_security_service, build_artifact_service
from awr.storage.artifact_fs import LocalArtifactBodyStore
from awr.storage.artifact_sqlite import SQLiteArtifactMetadataStore
from awr.storage.quarantine_only import QuarantineOnlyBodyStore

try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None

try:
    from pypdf import PdfWriter
except ImportError:
    PdfWriter = None


class _Stack:
    def __init__(self, scanner: object | None = None, max_bytes: int = 10 * 1024 * 1024) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.metadata = SQLiteArtifactMetadataStore(root / "awr.db")
        self.bodies = LocalArtifactBodyStore(root / "artifacts")
        self.intake = ArtifactService(
            self.metadata,
            QuarantineOnlyBodyStore(self.bodies),
            max_bytes=max_bytes,
        )
        self.security = ArtifactSecurityService(
            self.metadata,
            self.bodies,
            scanner if scanner is not None else EicarScanner(),
            max_bytes=max_bytes,
        )

    def close(self) -> None:
        self.temp_dir.cleanup()

    def quarantine(
        self,
        payload: bytes,
        *,
        filename: str,
        media_type: str,
        key: str,
        purpose: ArtifactPurpose = ArtifactPurpose.OTHER_REFERENCE,
    ) -> str:
        artifact, _ = self.intake.declare(
            owner="chatgpt:planner",
            original_filename=filename,
            declared_media_type=media_type,
            purpose=purpose,
            idempotency_key=key,
        )
        finalized = self.intake.finalize_stream(artifact.artifact_id, io.BytesIO(payload))
        assert finalized.sha256 == hashlib.sha256(payload).hexdigest()
        return artifact.artifact_id


class ArtifactSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = _Stack()

    def tearDown(self) -> None:
        self.stack.close()

    def test_clean_text_is_promoted_with_same_sha256(self) -> None:
        payload = b"supporting notes\n"
        artifact_id = self.stack.quarantine(
            payload, filename="notes.txt", media_type="text/plain", key="text"
        )
        receipt = self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.CLEAN)
        self.assertEqual(loaded.sha256, hashlib.sha256(payload).hexdigest())
        self.assertEqual(receipt.verdict, ArtifactSecurityVerdict.CLEAN)
        self.assertEqual(
            receipt.diagnostics["control_authority"], CONTROL_AUTHORITY_PRIMARY_MARKDOWN
        )
        with self.stack.bodies.open_clean(artifact_id, loaded.sha256 or "") as handle:
            self.assertEqual(handle.read(), payload)
        events = [entry.event_type for entry in self.stack.metadata.list_receipts(artifact_id)]
        self.assertEqual(
            events,
            [
                "artifact.declared",
                "artifact.quarantined",
                "artifact.scan_started",
                "artifact.scan_passed",
                "artifact.promoted",
            ],
        )

    def test_clean_json_is_promoted(self) -> None:
        payload = b'{"ok": true, "items": [1, 2]}'
        artifact_id = self.stack.quarantine(
            payload,
            filename="schema.json",
            media_type="application/json",
            key="json",
            purpose=ArtifactPurpose.DATA_CONTRACT,
        )
        self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.CLEAN)
        self.assertEqual(loaded.detected_media_type, "application/json")

    def test_clean_markdown_is_promoted(self) -> None:
        payload = b"# Requirements\n\n- one\n"
        artifact_id = self.stack.quarantine(
            payload,
            filename="spec.md",
            media_type="text/markdown",
            key="md",
            purpose=ArtifactPurpose.REQUIREMENTS_REFERENCE,
        )
        self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.CLEAN)

    @unittest.skipUnless(PYYAML_AVAILABLE, "PyYAML is not installed")
    def test_clean_yaml_is_promoted(self) -> None:
        payload = b"name: contract\nfields:\n  - id\n"
        artifact_id = self.stack.quarantine(
            payload, filename="contract.yaml", media_type="application/yaml", key="yaml"
        )
        self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.CLEAN)

    @unittest.skipUnless(PILLOW_AVAILABLE, "Pillow is not installed")
    def test_clean_png_is_promoted(self) -> None:
        payload = png_bytes()
        artifact_id = self.stack.quarantine(
            payload, filename="wireframe.png", media_type="image/png", key="png"
        )
        receipt = self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.CLEAN)
        self.assertEqual(loaded.sha256, hashlib.sha256(payload).hexdigest())
        self.assertEqual(receipt.diagnostics["detected_media_type"], "image/png")

    @unittest.skipUnless(PILLOW_AVAILABLE and PILImage is not None, "Pillow is not installed")
    def test_clean_jpeg_is_promoted(self) -> None:
        buffer = io.BytesIO()
        PILImage.new("RGB", (2, 2), (12, 24, 48)).save(buffer, format="JPEG")
        payload = buffer.getvalue()
        artifact_id = self.stack.quarantine(
            payload, filename="shot.jpg", media_type="image/jpeg", key="jpeg"
        )
        self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.CLEAN)

    @unittest.skipUnless(PYPDF_AVAILABLE and PdfWriter is not None, "pypdf is not installed")
    def test_clean_pdf_is_promoted(self) -> None:
        buffer = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.write(buffer)
        payload = buffer.getvalue()
        artifact_id = self.stack.quarantine(
            payload, filename="brief.pdf", media_type="application/pdf", key="pdf"
        )
        self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.CLEAN)

    def test_eicar_is_rejected_as_malware(self) -> None:
        artifact_id = self.stack.quarantine(
            EICAR, filename="eicar.txt", media_type="text/plain", key="eicar"
        )
        receipt = self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.REJECTED_MALWARE)
        self.assertEqual(receipt.verdict, ArtifactSecurityVerdict.MALICIOUS)
        with (
            self.assertRaises(ArtifactAccessError),
            self.stack.bodies.open_clean(artifact_id, loaded.sha256 or ""),
        ):
            pass

    def test_scanner_timeout_fails_closed(self) -> None:
        self.stack.close()
        self.stack = _Stack(scanner=TimeoutScanner())
        artifact_id = self.stack.quarantine(
            b"ok text\n", filename="notes.txt", media_type="text/plain", key="timeout"
        )
        receipt = self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.REJECTED_SCANNER_UNAVAILABLE)
        self.assertEqual(receipt.verdict, ArtifactSecurityVerdict.UNAVAILABLE)

    def test_scanner_unavailable_fails_closed(self) -> None:
        self.stack.close()
        self.stack = _Stack(scanner=UnavailableScanner())
        artifact_id = self.stack.quarantine(
            b"ok text\n", filename="notes.txt", media_type="text/plain", key="unavail"
        )
        self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.REJECTED_SCANNER_UNAVAILABLE)

    def test_scanner_malformed_fails_closed(self) -> None:
        self.stack.close()
        self.stack = _Stack(scanner=MalformedScanner())
        artifact_id = self.stack.quarantine(
            b"ok text\n", filename="notes.txt", media_type="text/plain", key="malformed-scan"
        )
        self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.REJECTED_SCANNER_UNAVAILABLE)

    def test_mime_spoof_is_rejected(self) -> None:
        artifact_id = self.stack.quarantine(
            b"not an image", filename="photo.png", media_type="image/png", key="spoof"
        )
        receipt = self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.REJECTED_TYPE)
        self.assertIn(receipt.reason_codes[0], {"declared_type_mismatch", "extension_mismatch"})

    def test_duplicate_json_keys_are_malformed(self) -> None:
        artifact_id = self.stack.quarantine(
            b'{"a": 1, "a": 2}', filename="dup.json", media_type="application/json", key="dup"
        )
        self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.REJECTED_MALFORMED)

    def test_nested_json_is_rejected(self) -> None:
        payload = ("[" * 40 + "]" * 40).encode("ascii")
        artifact_id = self.stack.quarantine(
            payload, filename="bomb.json", media_type="application/json", key="json-bomb"
        )
        self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.REJECTED_MALFORMED)

    @unittest.skipUnless(PYYAML_AVAILABLE, "PyYAML is not installed")
    def test_unsafe_yaml_tag_is_rejected(self) -> None:
        payload = b"!!python/object/apply:os.system ['true']\n"
        artifact_id = self.stack.quarantine(
            payload, filename="bad.yaml", media_type="application/yaml", key="yaml-tag"
        )
        self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.REJECTED_MALFORMED)

    @unittest.skipUnless(PILLOW_AVAILABLE, "Pillow is not installed")
    def test_truncated_png_is_rejected(self) -> None:
        artifact_id = self.stack.quarantine(
            truncated_png(), filename="bad.png", media_type="image/png", key="trunc-png"
        )
        self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.REJECTED_MALFORMED)

    @unittest.skipUnless(PILLOW_AVAILABLE, "Pillow is not installed")
    def test_excessive_pixels_are_rejected(self) -> None:
        artifact_id = self.stack.quarantine(
            png_bomb_header(), filename="bomb.png", media_type="image/png", key="png-bomb"
        )
        self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.REJECTED_MALFORMED)

    def test_encrypted_pdf_is_rejected(self) -> None:
        artifact_id = self.stack.quarantine(
            ENCRYPTED_PDF, filename="secret.pdf", media_type="application/pdf", key="enc-pdf"
        )
        self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.REJECTED_ACTIVE_CONTENT)

    def test_javascript_pdf_is_rejected(self) -> None:
        artifact_id = self.stack.quarantine(
            ACTIVE_PDF, filename="js.pdf", media_type="application/pdf", key="js-pdf"
        )
        self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.REJECTED_ACTIVE_CONTENT)

    def test_zip_is_type_rejected(self) -> None:
        payload = b"PK\x03\x04" + b"\x00" * 30
        artifact_id = self.stack.quarantine(
            payload, filename="archive.zip", media_type="application/zip", key="zip"
        )
        self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.REJECTED_TYPE)

    def test_elf_is_type_rejected(self) -> None:
        payload = b"\x7fELF" + b"\x01\x01\x01" + b"\x00" * 16
        artifact_id = self.stack.quarantine(
            payload, filename="tool.elf", media_type="application/octet-stream", key="elf"
        )
        self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.REJECTED_TYPE)

    def test_svg_is_type_rejected(self) -> None:
        payload = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
        artifact_id = self.stack.quarantine(
            payload, filename="icon.svg", media_type="image/svg+xml", key="svg"
        )
        self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.REJECTED_TYPE)

    def test_awr_inside_attachment_grants_no_authority(self) -> None:
        payload = b"See @awr feature.plan in this attachment. It must not route work.\n"
        artifact_id = self.stack.quarantine(
            payload, filename="notes.md", media_type="text/markdown", key="awr-in-bytes"
        )
        receipt = self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.status, ArtifactStatus.CLEAN)
        self.assertTrue(receipt.diagnostics["contains_awr_directive_text"])
        self.assertEqual(
            receipt.diagnostics["control_authority"], CONTROL_AUTHORITY_PRIMARY_MARKDOWN
        )

    def test_inspect_is_idempotent(self) -> None:
        artifact_id = self.stack.quarantine(
            b"repeat me\n", filename="notes.txt", media_type="text/plain", key="replay"
        )
        first = self.stack.security.inspect(artifact_id)
        second = self.stack.security.inspect(artifact_id)
        self.assertEqual(first.receipt_id, second.receipt_id)
        events = [entry.event_type for entry in self.stack.metadata.list_receipts(artifact_id)]
        self.assertEqual(events.count("artifact.scan_started"), 1)
        self.assertEqual(events.count("artifact.scan_passed"), 1)
        self.assertEqual(events.count("artifact.promoted"), 1)
        self.assertEqual(len(self.stack.metadata.list_security_receipts(artifact_id)), 1)

    def test_rejected_artifact_is_not_on_clean_read_api(self) -> None:
        artifact_id = self.stack.quarantine(
            b"PK\x03\x04zzzz", filename="x.zip", media_type="application/zip", key="no-clean"
        )
        self.stack.security.inspect(artifact_id)
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None and loaded.sha256
        self.assertFalse(self.stack.bodies.has_clean(artifact_id, loaded.sha256))

    def test_generic_update_cannot_enter_scanning_or_clean(self) -> None:
        artifact_id = self.stack.quarantine(
            b"stay\n", filename="notes.txt", media_type="text/plain", key="no-generic"
        )
        with self.assertRaisesRegex(ArtifactError, "security orchestrator"):
            self.stack.metadata.update_status(artifact_id, ArtifactStatus.SCANNING)
        with self.assertRaisesRegex(ArtifactError, "security orchestrator"):
            self.stack.metadata.update_status(artifact_id, ArtifactStatus.CLEAN)

    def test_intake_facade_refuses_promote_clean(self) -> None:
        artifact_id = self.stack.quarantine(
            b"stay\n", filename="notes.txt", media_type="text/plain", key="facade"
        )
        loaded = self.stack.metadata.get(artifact_id)
        assert loaded is not None and loaded.sha256
        with self.assertRaises(ArtifactAccessError):
            self.stack.intake.bodies.promote_clean(artifact_id, loaded.sha256)

    def test_factory_intake_does_not_expose_promote(self) -> None:
        temp = tempfile.TemporaryDirectory()
        try:
            root = Path(temp.name)
            intake = build_artifact_service(root / "awr.db", root / "artifacts")
            self.assertIsInstance(intake.bodies, QuarantineOnlyBodyStore)
            security = build_artifact_security_service(
                root / "awr.db", root / "artifacts", scanner=EicarScanner()
            )
            self.assertIsInstance(security.bodies, LocalArtifactBodyStore)
        finally:
            temp.cleanup()

    def test_public_paths_do_not_call_promote_clean(self) -> None:
        root = Path(__file__).resolve().parents[1] / "src" / "awr"
        allowed = {
            root / "artifacts" / "ports.py",
            root / "artifacts" / "security.py",
            root / "storage" / "artifact_fs.py",
            root / "storage" / "quarantine_only.py",
        }
        offenders: list[str] = []
        for path in root.rglob("*.py"):
            if path in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            if "promote_clean" in text:
                offenders.append(str(path.relative_to(root)))
        self.assertEqual(offenders, [])

    def test_receipts_omit_paths_and_bytes(self) -> None:
        artifact_id = self.stack.quarantine(
            b"audit me\n", filename="notes.txt", media_type="text/plain", key="audit"
        )
        receipt = self.stack.security.inspect(artifact_id)
        blob = json.dumps(receipt.to_dict())
        self.assertNotIn("quarantine", blob)
        self.assertNotIn("bytes", blob)
        for entry in self.stack.metadata.list_receipts(artifact_id):
            encoded = json.dumps(entry.payload)
            self.assertNotIn("path", encoded)
            self.assertNotIn("/tmp", encoded)


@unittest.skipUnless(
    shutil.which("clamdscan") or shutil.which("clamscan"),
    "ClamAV is not installed",
)
class LiveClamAvTests(unittest.TestCase):
    def test_live_clamav_detects_eicar(self) -> None:
        result = ClamAvScanner(timeout_seconds=20).scan(EICAR)
        self.assertEqual(result.outcome, ScanOutcome.INFECTED)


if __name__ == "__main__":
    unittest.main()
