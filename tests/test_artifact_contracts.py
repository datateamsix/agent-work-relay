from __future__ import annotations

import unittest

from awr.artifacts.contracts import (
    ArtifactPurpose,
    ArtifactReference,
    ArtifactSecurityReceipt,
    ArtifactSecurityVerdict,
    ArtifactStatus,
    allowed_transitions,
    status_from_security_receipt,
)


class ArtifactContractTests(unittest.TestCase):
    def test_purpose_vocabulary_is_closed(self) -> None:
        self.assertEqual(
            {purpose.value for purpose in ArtifactPurpose},
            {
                "design_reference",
                "data_contract",
                "requirements_reference",
                "other_reference",
            },
        )
        with self.assertRaises(ValueError):
            ArtifactPurpose("executor_override")

    def test_declared_can_quarantine_or_reject(self) -> None:
        allowed = allowed_transitions(ArtifactStatus.DECLARED)
        self.assertIn(ArtifactStatus.QUARANTINED, allowed)
        self.assertIn(ArtifactStatus.REJECTED_SIZE, allowed)
        self.assertIn(ArtifactStatus.REJECTED_TAMPERING, allowed)
        self.assertNotIn(ArtifactStatus.CLEAN, allowed)

    def test_rejection_and_relayed_are_terminal(self) -> None:
        for status in (
            ArtifactStatus.REJECTED_SIZE,
            ArtifactStatus.REJECTED_TYPE,
            ArtifactStatus.REJECTED_MALWARE,
            ArtifactStatus.REJECTED_ACTIVE_CONTENT,
            ArtifactStatus.REJECTED_MALFORMED,
            ArtifactStatus.REJECTED_TAMPERING,
            ArtifactStatus.REJECTED_SCANNER_UNAVAILABLE,
            ArtifactStatus.RELAYED,
        ):
            self.assertTrue(status.terminal)
            self.assertEqual(allowed_transitions(status), frozenset())

    def test_reference_omits_bytes_and_urls(self) -> None:
        reference = ArtifactReference(
            artifact_id="ART-1",
            purpose=ArtifactPurpose.DATA_CONTRACT,
            byte_length=4,
            sha256="a" * 64,
            detected_media_type=None,
            safe_filename="schema.json",
        )
        payload = reference.to_dict()
        self.assertNotIn("bytes", payload)
        self.assertNotIn("url", payload)
        self.assertNotIn("path", payload)

    def test_security_receipt_reason_maps_active_and_malformed(self) -> None:
        malformed = ArtifactSecurityReceipt(
            receipt_id="scr-1",
            artifact_id="ART-1",
            scanner_id="policy",
            scanner_version="0",
            signature_version="0",
            verdict=ArtifactSecurityVerdict.INCONCLUSIVE,
            reason_codes=("malformed",),
            scanned_sha256="a" * 64,
            started_at="2026-08-28T00:00:00+00:00",
            completed_at="2026-08-28T00:00:00+00:00",
            diagnostics={},
        )
        self.assertEqual(status_from_security_receipt(malformed), ArtifactStatus.REJECTED_MALFORMED)
        active = ArtifactSecurityReceipt(
            receipt_id="scr-2",
            artifact_id="ART-1",
            scanner_id="policy",
            scanner_version="0",
            signature_version="0",
            verdict=ArtifactSecurityVerdict.INCONCLUSIVE,
            reason_codes=("active_content",),
            scanned_sha256="a" * 64,
            started_at="2026-08-28T00:00:00+00:00",
            completed_at="2026-08-28T00:00:00+00:00",
            diagnostics={},
        )
        self.assertEqual(
            status_from_security_receipt(active), ArtifactStatus.REJECTED_ACTIVE_CONTENT
        )


if __name__ == "__main__":
    unittest.main()
