from __future__ import annotations

import os
import subprocess
import tempfile
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
        parsed = subprocess.run(
            ["bash", "-n", str(SCRIPT)], check=False, capture_output=True, text=True
        )
        self.assertEqual(parsed.returncode, 0, parsed.stderr)

    def test_help_lists_issuer_and_never_mentions_static_tokens(self) -> None:
        help_text = subprocess.check_output([str(SCRIPT), "--help"], text=True)
        self.assertIn("--issuer", help_text)
        self.assertIn("PROJECT_ID", help_text)
        self.assertIn("AWR_REPOSITORY_URL", help_text)
        self.assertIn("FIRESTORE_LOCATION", help_text)
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
        self.assertIn(
            "gcloud projects describe \"${PROJECT_ID}\" --format='value(projectNumber)'", setup
        )
        self.assertIn("firestore databases describe", setup)
        self.assertIn("firestore databases create", setup)
        self.assertIn("firestore databases update", setup)
        self.assertIn("--type firestore-native", setup)
        self.assertIn("--delete-protection", setup)
        self.assertIn("builds get-default-service-account", setup)
        self.assertNotIn("roles/run.admin", setup)
        self.assertNotIn("compute@developer.gserviceaccount.com", setup)
        self.assertIn("artifacts repositories add-iam-policy-binding", setup)
        indexes = INDEXES.read_text(encoding="utf-8")
        self.assertIn("indexes composite create", indexes)
        self.assertIn("indexes fields update", indexes)

    def test_operator_config_is_explicit_and_old_defaults_are_absent(self) -> None:
        paths = (SCRIPT, SETUP, ROOT / "deploy" / "gcp_deploy.sh", INDEXES)
        combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        self.assertNotIn("modelready-m3", combined)
        self.assertNotIn("datateamsix/engineering-work-broker", combined)

        env = os.environ.copy()
        env.pop("PROJECT_ID", None)
        env.pop("AWR_REPOSITORY_URL", None)
        result = subprocess.run(
            [str(SCRIPT), "--skip-auth", "--issuer", "https://tenant.example/"],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Set PROJECT_ID", result.stderr)

    def test_fresh_project_setup_creates_native_firestore_and_uses_default_build_sa(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            log = root / "gcloud.log"
            fake_gcloud = root / "gcloud"
            fake_gcloud.write_text(
                """#!/usr/bin/env bash
printf '%s\\n' \"$*\" >> \"${GCLOUD_LOG}\"
case \"$1 $2\" in
  \"projects describe\") printf '123456789\\n' ;;
  \"firestore databases describe\") exit 1 ;;
  \"builds get-default-service-account\") printf 'build@example.iam.gserviceaccount.com\\n' ;;
esac
""",
                encoding="utf-8",
            )
            fake_gcloud.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{root}:{env['PATH']}",
                    "GCLOUD_LOG": str(log),
                    "PROJECT_ID": "awr-test-project",
                    "REGION": "us-central1",
                    "FIRESTORE_DATABASE": "(default)",
                    "FIRESTORE_LOCATION": "us-central1",
                }
            )
            result = subprocess.run(
                [str(SETUP)], check=False, capture_output=True, text=True, env=env
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = log.read_text(encoding="utf-8")
            self.assertIn("firestore databases create", calls)
            self.assertIn("--type firestore-native", calls)
            self.assertIn("--delete-protection", calls)
            self.assertIn("builds get-default-service-account", calls)
            self.assertIn("serviceAccount:build@example.iam.gserviceaccount.com", calls)

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
