from __future__ import annotations

import unittest

from awr.artifacts.contracts import (
    ArtifactPurpose,
    ArtifactReference,
    ArtifactStatus,
    allowed_transitions,
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


if __name__ == "__main__":
    unittest.main()
