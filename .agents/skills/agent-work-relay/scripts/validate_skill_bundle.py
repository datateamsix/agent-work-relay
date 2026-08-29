#!/usr/bin/env python3
"""Deterministic validation for the Agent Work Relay skill bundle."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from skill_paths import (
    BASELINE_INPUT_MUTATIONS,
    BASELINE_TOOLS,
    EX01_TOOLS,
    EXAMPLES_GT003,
    INPUT_INTENTS,
    KNOWN_SCOPES,
    MANIFEST_PATH,
    OPENAI_YAML,
    OPERATIONAL_INPUT_INTENTS,
    READ_ONLY_TOOLS,
    REFERENCES_DIR,
    RESPONSE_SCHEMA_PATH,
    RESPONSE_TYPES,
    SKILL_MD,
    SKILL_ROOT,
    TEMPLATES_DIR,
)
from template_io import (
    FRONTMATTER_SECRET_RE,
    UNSUPPORTED_CLAIM_RE,
    load_manifest,
    markdown_links,
    parse_skill_frontmatter,
    parse_template,
    sha256_file,
    template_direction,
)
from validate_gt003 import validate_gt003_fixtures

SCAFFOLD_RE = (
    "TODO",
    "FIXME",
    "TBD",
    "lorem ipsum",
    "Your Name Here",
    "xxx placeholder",
)


def validate_bundle() -> list[str]:
    errors: list[str] = []
    errors.extend(_validate_skill_md())
    errors.extend(_validate_openai_yaml())
    errors.extend(_validate_manifest_and_templates())
    errors.extend(_validate_reference_routing())
    errors.extend(_validate_claims(SKILL_ROOT))
    if EXAMPLES_GT003.exists():
        errors.extend(validate_gt003_fixtures())
    return errors


def _validate_skill_md() -> list[str]:
    errors: list[str] = []
    if not SKILL_MD.exists():
        return [f"Missing {SKILL_MD}"]
    text = SKILL_MD.read_text(encoding="utf-8")
    try:
        meta = parse_skill_frontmatter(text)
    except ValueError as exc:
        return [str(exc)]
    if meta.get("name") != "agent-work-relay":
        errors.append("SKILL.md name must be agent-work-relay.")
    description = meta.get("description", "")
    if len(description) < 40:
        errors.append("SKILL.md description must discriminate when to use the skill.")
    if "do not" not in description.lower():
        errors.append("SKILL.md description should say when not to invoke the skill.")
    return errors


def _validate_openai_yaml() -> list[str]:
    errors: list[str] = []
    if not OPENAI_YAML.exists():
        return ["Missing agents/openai.yaml"]
    text = OPENAI_YAML.read_text(encoding="utf-8")
    if "interface:" not in text or "dependencies:" not in text:
        errors.append("agents/openai.yaml must declare interface and dependencies.")
    if 'type: "mcp"' not in text and "type: mcp" not in text:
        errors.append("agents/openai.yaml must declare an MCP dependency.")
    if "agent-work-relay" not in text:
        errors.append("agents/openai.yaml must name the AWR MCP server.")
    return errors


def _validate_manifest_and_templates() -> list[str]:
    errors: list[str] = []
    manifest = load_manifest(MANIFEST_PATH)
    templates = manifest.get("templates")
    if not isinstance(templates, list) or not templates:
        return ["Manifest templates[] is required."]
    ids: list[str] = []
    listed_files: set[str] = set()
    schema = _load_response_schema()
    for item in templates:
        if not isinstance(item, dict):
            errors.append("Each manifest entry must be an object.")
            continue
        for field in (
            "id",
            "template_version",
            "direction",
            "intent_or_response_type",
            "applicable_roles",
            "schema_version",
            "required_mcp_capability",
            "required_oauth_scope",
            "minimum_lifecycle_capability",
            "operational_status",
            "file",
            "sha256",
        ):
            if field not in item:
                errors.append(f"Manifest entry missing {field}.")
        template_id = str(item.get("id") or "")
        ids.append(template_id)
        rel = str(item.get("file") or "")
        listed_files.add(Path(rel).name)
        path = SKILL_ROOT / "assets" / rel
        if not path.exists():
            errors.append(f"Missing template file: {rel}")
            continue
        digest = sha256_file(path)
        if item.get("sha256") != digest:
            errors.append(f"SHA-256 mismatch for {template_id}: manifest has a stale fingerprint.")
        try:
            parsed = parse_template(path)
            direction = template_direction(parsed["envelope"], parsed["decorator"])
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if item.get("direction") != direction:
            errors.append(f"{template_id} direction does not match decorator.")
        lifecycle = str(item.get("intent_or_response_type") or "")
        envelope = parsed["envelope"]
        actual = envelope.get("intent") or envelope.get("response_type")
        if actual != lifecycle:
            errors.append(f"{template_id} lifecycle type {actual!r} != manifest {lifecycle!r}.")
        scope = str(item.get("required_oauth_scope") or "")
        if scope not in KNOWN_SCOPES:
            errors.append(f"{template_id} has unknown OAuth scope {scope}.")
        status = str(item.get("operational_status") or "")
        capability = str(item.get("minimum_lifecycle_capability") or "")
        if capability == "baseline" and status != "operational":
            errors.append(f"{template_id} is baseline but not marked operational.")
        if capability == "ex-01" and status != "prepared":
            errors.append(f"{template_id} must be prepared, not claimed operational, until EX-01.")
        if capability == "submit_input" and status != "prepared":
            errors.append(f"{template_id} requires submit_input and must stay prepared.")
        if capability == "as-04":
            errors.append(f"{template_id} must not require AS-04.")
        if direction == "input":
            errors.extend(_validate_input_capability(template_id, item, lifecycle, status))
        if direction == "response":
            errors.extend(_validate_response_template(template_id, envelope, schema))
        if FRONTMATTER_SECRET_RE.search(parsed["text"]):
            errors.append(f"{path.name} contains a token-like value.")
        lowered = parsed["text"].lower()
        if any(token.lower() in lowered for token in SCAFFOLD_RE):
            errors.append(f"{path.name} contains an unfinished scaffold placeholder.")
    if len(ids) != len(set(ids)):
        errors.append("Manifest template IDs must be unique.")
    disk_files = {path.name for path in TEMPLATES_DIR.glob("*.md")}
    missing = disk_files - listed_files
    extra = listed_files - disk_files
    if missing:
        errors.append(f"Orphaned template files: {sorted(missing)}")
    if extra:
        errors.append(f"Manifest lists missing files: {sorted(extra)}")
    return errors


def _validate_input_capability(
    template_id: str, item: dict[str, Any], lifecycle: str, status: str
) -> list[str]:
    errors: list[str] = []
    required_tool = str(item.get("required_mcp_capability") or "")
    missing = str(item.get("missing_capability") or "").strip()
    if status == "operational":
        if required_tool in READ_ONLY_TOOLS:
            errors.append(
                f"{template_id} is operational but required_mcp_capability "
                f"{required_tool} is read-only and cannot transmit the packet."
            )
        if required_tool not in BASELINE_INPUT_MUTATIONS:
            errors.append(
                f"{template_id} is operational but {required_tool} is not a "
                "public baseline mutation that accepts this input."
            )
        if lifecycle not in OPERATIONAL_INPUT_INTENTS:
            errors.append(
                f"{template_id} ({lifecycle}) must stay prepared until a "
                "public mutation tool can accept that packet."
            )
    if status == "prepared":
        if not missing:
            errors.append(
                f"{template_id} is prepared but does not identify the missing "
                "capability or work-slice dependency."
            )
        if required_tool in READ_ONLY_TOOLS:
            errors.append(
                f"{template_id} is prepared but still lists a read-only "
                "required_mcp_capability; name the future mutation instead."
            )
    return errors


def _validate_response_template(
    template_id: str, envelope: dict[str, str], schema: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    if envelope.get("authority") != "report_only":
        errors.append(f"{template_id} must set authority: report_only.")
    required = schema.get("required")
    if isinstance(required, list):
        for field in required:
            if field == "payload":
                continue
            if field not in envelope:
                errors.append(f"{template_id} is missing required response field {field}.")
    response_type = envelope.get("response_type")
    enum_values = (
        schema.get("properties", {}).get("response_type", {}).get("enum")
        if isinstance(schema.get("properties"), dict)
        else None
    )
    if isinstance(enum_values, list) and response_type not in enum_values:
        errors.append(f"{template_id} response_type is not in awr.response/v1.")
    return errors


def _validate_reference_routing() -> list[str]:
    errors: list[str] = []
    skill_text = SKILL_MD.read_text(encoding="utf-8")
    routed = {
        Path(link).name for link in markdown_links(skill_text) if link.startswith("references/")
    }
    extra_texts = [skill_text]
    for path in REFERENCES_DIR.glob("*.md"):
        extra_texts.append(path.read_text(encoding="utf-8"))
        name = path.name
        if name == "customization.md":
            if "customization.md" not in routed and not any(
                "customization.md" in text for text in extra_texts
            ):
                errors.append("customization.md is not linked from SKILL.md or a routed reference.")
            continue
        mentioned = any(name in text for text in extra_texts)
        if not mentioned:
            errors.append(
                f"Reference {name} is not linked from SKILL.md or another routed reference."
            )
    return errors


def _validate_claims(root: Path) -> list[str]:
    errors: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in {".md", ".json", ".yaml", ".yml"}:
            continue
        if "scripts" in path.parts and path.suffix == ".py":
            continue
        text = path.read_text(encoding="utf-8")
        if FRONTMATTER_SECRET_RE.search(text):
            errors.append(f"{path.relative_to(SKILL_ROOT)} contains a token-like example value.")
        if UNSUPPORTED_CLAIM_RE.search(text):
            errors.append(
                f"{path.relative_to(SKILL_ROOT)} makes an unsupported delivery or MCP claim."
            )
        if "LC-01 response tools are not implemented" in text:
            errors.append(
                f"{path.relative_to(SKILL_ROOT)} still says LC-01 tools are unimplemented."
            )
        if "EX-01 has merged" in text:
            errors.append(f"{path.relative_to(SKILL_ROOT)} falsely claims EX-01 has merged.")
    return errors


def _load_response_schema() -> dict[str, Any]:
    loaded = json.loads(RESPONSE_SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError("awr.response/v1 schema must be an object.")
    return loaded


def main() -> int:
    errors = validate_bundle()
    if EX01_TOOLS & BASELINE_TOOLS:
        errors.append("EX-01 tools must not be listed as baseline.")
    if INPUT_INTENTS and RESPONSE_TYPES:
        pass
    if errors:
        print("Skill bundle validation failed:")
        for item in errors:
            print(f"- {item}")
        return 1
    print("Skill bundle validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
