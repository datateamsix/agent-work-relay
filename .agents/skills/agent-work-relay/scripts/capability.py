"""Classify AWR MCP tools by baseline, EX-01, and AS-04 capability."""

from __future__ import annotations

from skill_paths import BASELINE_TOOLS, EX01_TOOLS

AS04_CAPABILITIES = frozenset(
    {
        "deliver_clean_artifact_bytes",
        "gcs_clean_object_delivery",
        "signed_artifact_access",
        "capability_aware_binary_materialization",
        "artifact_delivery_acknowledgement",
    }
)

CapabilityLevel = str


def classify_tool(name: str) -> CapabilityLevel:
    if name in BASELINE_TOOLS:
        return "baseline"
    if name in EX01_TOOLS:
        return "ex-01"
    if name in AS04_CAPABILITIES:
        return "as-04"
    return "unknown"


def mutation_ready(tool: str, available: set[str]) -> bool:
    """True only when the connected server lists the required mutation tool."""
    return tool in available and classify_tool(tool) != "unknown"


def missing_capability(tool: str, available: set[str]) -> str | None:
    if tool in available:
        return None
    level = classify_tool(tool)
    if level == "unknown":
        return f"unknown tool {tool}"
    return level
