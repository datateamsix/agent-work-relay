from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from awr.responses import ResponsePacketError, parse_response_markdown, parse_response_packet
from awr.responses.canonical import fingerprint_packet

REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = REPO_ROOT / ".agents" / "skills" / "agent-work-relay" / "scripts"


def _load_script(name: str) -> ModuleType:
    path = _SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"awr_sr01_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_skill_paths = _load_script("skill_paths")
_template_io = _load_script("template_io")
_capability = _load_script("capability")
_validate_gt003 = _load_script("validate_gt003")
_validate_bundle = _load_script("validate_skill_bundle")

BASELINE_TOOLS = _skill_paths.BASELINE_TOOLS
EX01_TOOLS = _skill_paths.EX01_TOOLS
EXAMPLES_GT003 = _skill_paths.EXAMPLES_GT003
MANIFEST_PATH = _skill_paths.MANIFEST_PATH
OPERATIONAL_INPUT_INTENTS = _skill_paths.OPERATIONAL_INPUT_INTENTS
READ_ONLY_TOOLS = _skill_paths.READ_ONLY_TOOLS
SKILL_ROOT = _skill_paths.SKILL_ROOT
TEMPLATES_DIR = _skill_paths.TEMPLATES_DIR
classify_tool = _capability.classify_tool
missing_capability = _capability.missing_capability
mutation_ready = _capability.mutation_ready
FRONTMATTER_SECRET_RE = _template_io.FRONTMATTER_SECRET_RE
UNSUPPORTED_CLAIM_RE = _template_io.UNSUPPORTED_CLAIM_RE
load_manifest = _template_io.load_manifest
parse_template = _template_io.parse_template
sha256_file = _template_io.sha256_file
template_direction = _template_io.template_direction
EXPECTED_KINDS = _validate_gt003.EXPECTED_KINDS
validate_gt003_fixtures = _validate_gt003.validate_gt003_fixtures
validate_bundle = _validate_bundle.validate_bundle


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
        assert item["template_version"] >= 2


def test_operational_inputs_have_public_mutation_paths() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    operational_inputs: list[str] = []
    for item in manifest["templates"]:
        if item["direction"] != "input":
            continue
        if item["operational_status"] == "operational":
            operational_inputs.append(item["intent_or_response_type"])
            assert item["required_mcp_capability"] not in READ_ONLY_TOOLS
            assert item["required_mcp_capability"] in {
                "submit_prompt_for_planning",
                "submit_work_bundle_for_planning",
            }
            assert item["intent_or_response_type"] in OPERATIONAL_INPUT_INTENTS
        else:
            assert item["operational_status"] == "prepared"
            assert item.get("missing_capability")
            assert item["required_mcp_capability"] not in READ_ONLY_TOOLS
    assert set(operational_inputs) == OPERATIONAL_INPUT_INTENTS


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
    blocked = (TEMPLATES_DIR / "response-question-blocked.md").read_text(encoding="utf-8")
    assert "- q1:" in blocked
    assert "# Blocking questions" in blocked


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
    assert ids["review_outcome"] == "APPROVED"
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
    assert "capability-detected" in worker
    assert "exposes outbound AWR MCP" in worker


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


def test_pytest_pythonpath_does_not_include_skill_scripts() -> None:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert ".agents/skills/agent-work-relay/scripts" not in text
    assert 'pythonpath = ["src", "tests"]' in text
