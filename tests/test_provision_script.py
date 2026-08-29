from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "provision_cloud_run.sh"
SETUP = ROOT / "deploy" / "gcp_setup.sh"
INDEXES = ROOT / "deploy" / "apply_firestore_indexes.sh"


class ProvisionScriptTests(unittest.TestCase):
    def test_script_is_executable_and_parses(self) -> None:
        self.assertTrue(SCRIPT.is_file())
        self.assertTrue(SCRIPT.stat().st_mode & 0o111)
        parsed = subprocess.run(["bash", "-n", str(SCRIPT)], check=False, capture_output=True, text=True)
        self.assertEqual(parsed.returncode, 0, parsed.stderr)

    def test_help_lists_issuer_and_never_mentions_static_tokens(self) -> None:
        help_text = subprocess.check_output([str(SCRIPT), "--help"], text=True)
        self.assertIn("--issuer", help_text)
        self.assertIn("CURSOR_API_KEY_FILE", help_text)
        self.assertNotIn("AWR_STATIC_TOKEN", help_text)

    def test_deploy_helpers_parse_and_avoid_invalid_firestore_import(self) -> None:
        for path in (SETUP, INDEXES, SCRIPT):
            self.assertTrue(path.is_file(), path)
            parsed = subprocess.run(
                ["bash", "-n", str(path)], check=False, capture_output=True, text=True
            )
            self.assertEqual(parsed.returncode, 0, parsed.stderr)
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("indexes composite import", text)
            self.assertNotIn("912257136465", text)
        setup = SETUP.read_text(encoding="utf-8")
        self.assertIn('gcloud projects describe "${PROJECT_ID}" --format=\'value(projectNumber)\'', setup)
        self.assertNotIn("roles/run.admin", setup)
        self.assertNotIn("compute@developer.gserviceaccount.com", setup)
        self.assertIn("artifacts repositories add-iam-policy-binding", setup)
        indexes = INDEXES.read_text(encoding="utf-8")
        self.assertIn("indexes composite create", indexes)
        self.assertIn("indexes fields update", indexes)

    def test_unknown_flag_fails_closed(self) -> None:
        result = subprocess.run(
            [str(SCRIPT), "--not-a-flag"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown option", result.stderr)


if __name__ == "__main__":
    unittest.main()
