#!/usr/bin/env python3
"""Refresh exact SHA-256 fingerprints in template-manifest.json."""

from __future__ import annotations

import json
import sys

from skill_paths import MANIFEST_PATH, SKILL_ROOT
from template_io import load_manifest, sha256_file


def refresh_manifest() -> dict[str, str]:
    manifest = load_manifest(MANIFEST_PATH)
    templates = manifest.get("templates")
    if not isinstance(templates, list):
        raise TypeError("Manifest templates[] is required.")
    updated: dict[str, str] = {}
    for item in templates:
        if not isinstance(item, dict):
            raise TypeError("Each manifest entry must be an object.")
        path = SKILL_ROOT / "assets" / str(item["file"])
        digest = sha256_file(path)
        if item.get("sha256") != digest:
            updated[str(item["id"])] = digest
        item["sha256"] = digest
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return updated


def main() -> int:
    updated = refresh_manifest()
    if updated:
        print(f"Updated {len(updated)} template fingerprint(s).")
        for template_id, digest in updated.items():
            print(f"- {template_id}: {digest}")
    else:
        print("Template fingerprints already match exact file bytes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
