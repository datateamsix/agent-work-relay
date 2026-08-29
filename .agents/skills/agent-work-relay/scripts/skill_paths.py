"""Stable paths for the Agent Work Relay skill bundle."""

from __future__ import annotations

from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SKILL_ROOT.parents[2]
TEMPLATES_DIR = SKILL_ROOT / "assets" / "templates"
MANIFEST_PATH = SKILL_ROOT / "assets" / "template-manifest.json"
SKILL_MD = SKILL_ROOT / "SKILL.md"
OPENAI_YAML = SKILL_ROOT / "agents" / "openai.yaml"
REFERENCES_DIR = SKILL_ROOT / "references"
RESPONSE_SCHEMA_PATH = REPO_ROOT / "schemas" / "awr.response.v1.json"
EXAMPLES_GT003 = REPO_ROOT / "examples" / "AWR-GT-003"

INPUT_INTENTS = frozenset(
    {
        "feature.plan",
        "bugfix.plan",
        "refinement.plan",
        "plan.revise",
        "plan.execute",
        "question.answer",
        "implementation.refine",
        "completion.review",
    }
)
RESPONSE_TYPES = frozenset(
    {
        "receipt.accepted",
        "plan.completed",
        "question.blocked",
        "execution.acknowledged",
        "execution.progress",
        "execution.completed",
        "execution.failed",
        "review.completed",
    }
)
BASELINE_TOOLS = frozenset(
    {
        "submit_prompt_for_planning",
        "submit_work_bundle_for_planning",
        "begin_artifact_intake",
        "finalize_artifact_upload",
        "get_artifact_status",
        "get_work_order_artifacts",
        "refresh_planning",
        "get_plan",
        "submit_response",
        "record_decision",
        "get_work_order",
        "get_work_order_timeline",
        "list_pending_actions",
    }
)
EX01_TOOLS = frozenset({"refresh_external_run"})
READ_ONLY_TOOLS = frozenset(
    {
        "get_work_order",
        "get_work_order_timeline",
        "list_pending_actions",
        "get_plan",
        "get_artifact_status",
        "get_work_order_artifacts",
    }
)
BASELINE_INPUT_MUTATIONS = frozenset(
    {
        "submit_prompt_for_planning",
        "submit_work_bundle_for_planning",
    }
)
KNOWN_SCOPES = frozenset({"awr:plan", "awr:read", "awr:refresh", "awr:response", "awr:decide"})
OPERATIONAL_INPUT_INTENTS = frozenset({"feature.plan", "refinement.plan"})
