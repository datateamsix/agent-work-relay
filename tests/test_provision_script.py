from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "provision_cloud_run.sh"


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
