from __future__ import annotations

import json
from pathlib import Path

from capability import classify_tool, missing_capability, mutation_ready
from skill_paths import (
    BASELINE_TOOLS,
    EX01_TOOLS,
    EXAMPLES_GT003,
    MANIFEST_PATH,
    SKILL_ROOT,
    TEMPLATES_DIR,
)
from template_io import (
    FRONTMATTER_SECRET_RE,
    UNSUPPORTED_CLAIM_RE,
    load_manifest,
    parse_template,
    sha256_file,
    template_direction,
)
from validate_gt003 import EXPECTED_KINDS, validate_gt003_fixtures
from validate_skill_bundle import validate_bundle

from awr.responses import ResponsePacketError, parse_response_markdown, parse_response_packet
from awr.responses.canonical import fingerprint_packet

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_skill_bundle_validator_passes() -> None:
    assert validate_bundle() == []


def test_manifest_ids_unique_and_hashes_match() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    templates = manifest["templates"]
    ids = [item["id"] for item in templates]
    assert len(ids) == len(set(ids))
    assert len(templates) == 16
    for item in templates:
        path = SKILL_ROOT / "assets" / item["file"]
        assert item["sha256"] == sha256_file(path)
        assert item["template_version"] == 2


def test_template_reference_routing_and_decorators() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    for name in (
        "planner-workflows.md",
        "worker-workflows.md",
        "reviewer-workflows.md",
        "adapter-workflows.md",
        "human-decisions.md",
        "capability.md",
        "installation.md",
    ):
        assert f"references/{name}" in skill
    for path in TEMPLATES_DIR.glob("*.md"):
        parsed = parse_template(path)
        direction = template_direction(parsed["envelope"], parsed["decorator"])
        assert parsed["decorator"] in {"@input", "@response"}
        assert direction in {"input", "response"}
        if direction == "response":
            assert parsed["envelope"]["authority"] == "report_only"
            assert parsed["envelope"]["schema"] == "awr.response/v1"
            assert parsed["envelope"]["in_reply_to"]


def test_response_templates_align_with_runtime_schema() -> None:
    schema = json.loads(
        (REPO_ROOT / "schemas" / "awr.response.v1.json").read_text(encoding="utf-8")
    )
    required = [field for field in schema["required"] if field != "payload"]
    enum_values = schema["properties"]["response_type"]["enum"]
    for path in TEMPLATES_DIR.glob("response-*.md"):
        envelope = parse_template(path)["envelope"]
        for field in required:
            assert field in envelope, f"{path.name} missing {field}"
        assert envelope["response_type"] in enum_values
        assert envelope["authority"] == "report_only"


def test_capability_gating_does_not_claim_ex01_or_as04() -> None:
    assert EX01_TOOLS.isdisjoint(BASELINE_TOOLS)
    assert classify_tool("submit_response") == "baseline"
    assert classify_tool("refresh_external_run") == "ex-01"
    assert classify_tool("deliver_clean_artifact_bytes") == "as-04"
    available = set(BASELINE_TOOLS)
    assert mutation_ready("submit_response", available)
    assert not mutation_ready("refresh_external_run", available)
    assert missing_capability("refresh_external_run", available) == "ex-01"
    assert missing_capability("deliver_clean_artifact_bytes", available) == "as-04"
    capability = (SKILL_ROOT / "references" / "capability.md").read_text(encoding="utf-8")
    assert "Prepared, not operational until EX-01" in capability
    assert "Unavailable until AS-04" in capability
    assert "refresh_external_run" in capability


def test_stable_idempotency_examples() -> None:
    text = (SKILL_ROOT / "references" / "idempotency.md").read_text(encoding="utf-8")
    examples = [
        "awr:gt003:feature.plan:v1",
        "awr:AWR-1001:plan.completed:v1",
        "awr:AWR-1001:approve_plan:v1",
        "awr:AWR-1001:execution.completed:v1",
    ]
    for key in examples:
        assert key in text
        parts = key.split(":")
        assert len(parts) >= 4
        assert parts[0] == "awr"
        assert "T" not in parts[-1]
    assert examples[1] != examples[3]
    manifest = load_manifest(MANIFEST_PATH)
    keys = {
        parse_template(SKILL_ROOT / "assets" / item["file"])["envelope"]["idempotency_key"]
        for item in manifest["templates"]
    }
    assert len(keys) == 16


def test_gt003_lineage_and_expected_timeline() -> None:
    assert validate_gt003_fixtures() == []
    ids = json.loads((EXAMPLES_GT003 / "fixture-ids.json").read_text(encoding="utf-8"))
    timeline = json.loads(
        (EXAMPLES_GT003 / "12-expected-timeline.json").read_text(encoding="utf-8")
    )
    assert [row["kind"] for row in timeline] == list(EXPECTED_KINDS)
    work_order_id = ids["work_order_id"]
    assert all(row["work_order_id"] == work_order_id for row in timeline)
    completed = parse_response_markdown(
        (EXAMPLES_GT003 / "08-execution-completed.response.md").read_text(encoding="utf-8")
    )
    review = parse_response_markdown(
        (EXAMPLES_GT003 / "10-review-completed.response.md").read_text(encoding="utf-8")
    )
    plan = parse_response_markdown(
        (EXAMPLES_GT003 / "03-plan-completed.response.md").read_text(encoding="utf-8")
    )
    assert plan.content_sha256 == fingerprint_packet(plan)
    assert plan.content_sha256 == ids["plan_sha256"]
    assert completed.executor_run_id == ids["executor_run_id"]
    assert completed.work_order_id == work_order_id
    assert review.authority == "report_only"
    assert review.payload["outcome"] == "APPROVED"
    assert ids["repository_url"] == "https://github.com/example/fixture-relay"


def test_no_embedded_secrets_or_unsupported_claims() -> None:
    forbidden_cursor = (
        "cursor cloud must call",
        "cursor cloud should call",
        "direct outbound mcp is required for cursor",
    )
    forbidden_delivery = (
        "executors receive artifact bytes",
        "artifact bytes are delivered",
        "ex-01 has merged",
        "lc-01 response tools are not implemented",
    )
    paths: list[Path] = []
    paths.extend(SKILL_ROOT.rglob("*"))
    paths.append(REPO_ROOT / "docs" / "AGENT_WORK_RELAY_SKILLS_BUNDLE.md")
    paths.append(REPO_ROOT / "docs" / "AWR-SR-01_INTEGRATION_NOTES.md")
    if EXAMPLES_GT003.exists():
        paths.extend(EXAMPLES_GT003.glob("*"))
    for path in paths:
        if not path.is_file() or path.suffix not in {".md", ".json", ".yaml", ".yml"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert FRONTMATTER_SECRET_RE.search(text) is None, path
        assert UNSUPPORTED_CLAIM_RE.search(text) is None, path
        lowered = text.lower()
        for phrase in forbidden_cursor + forbidden_delivery:
            assert phrase not in lowered, f"{path} contains {phrase}"
    worker = (SKILL_ROOT / "references" / "worker-workflows.md").read_text(encoding="utf-8")
    assert "outbound MCP is not required for Cursor Cloud" in worker
    assert "Never require Cursor Cloud to open an outbound MCP connection." in worker


def test_runtime_parser_rejects_authority_and_unknown_type() -> None:
    completed = parse_response_markdown(
        (EXAMPLES_GT003 / "08-execution-completed.response.md").read_text(encoding="utf-8")
    )
    payload = completed.to_dict()
    payload["authority"] = "execution"
    payload.pop("content_sha256", None)
    try:
        parse_response_packet(payload)
    except ResponsePacketError as exc:
        assert "never grant" in str(exc)
    else:
        raise AssertionError("authority override must fail closed")
