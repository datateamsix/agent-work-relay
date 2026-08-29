#!/usr/bin/env python3
"""Validate AWR-GT-003 fixture lineage, fingerprints, and safety."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from skill_paths import EXAMPLES_GT003
from template_io import FRONTMATTER_SECRET_RE, UNSUPPORTED_CLAIM_RE

from awr.responses import parse_response_markdown
from awr.responses.canonical import fingerprint_packet

REQUIRED_FILES = (
    "01-feature-plan.input.md",
    "02-receipt-accepted.response.md",
    "03-plan-completed.response.md",
    "04-plan-approval.decision-request.md",
    "04-plan-approval.decision.json",
    "05-plan-execute.input.md",
    "06-execution-acknowledged.response.md",
    "07-execution-progress.response.md",
    "08-execution-completed.response.md",
    "09-completion-review.input.md",
    "10-review-completed.response.md",
    "11-completion-acceptance.decision-request.md",
    "11-completion-acceptance.decision.json",
    "12-expected-timeline.json",
    "fixture-ids.json",
)

REQUIRED_ID_KEYS = (
    "work_order_id",
    "plan_id",
    "plan_sha256",
    "source_input_sha256",
    "executor_run_id",
    "repository_url",
)

RESPONSE_FILES = (
    ("02-receipt-accepted.response.md", "receipt.accepted"),
    ("03-plan-completed.response.md", "plan.completed"),
    ("06-execution-acknowledged.response.md", "execution.acknowledged"),
    ("07-execution-progress.response.md", "execution.progress"),
    ("08-execution-completed.response.md", "execution.completed"),
    ("10-review-completed.response.md", "review.completed"),
)

EXPECTED_KINDS = (
    "work_order.accepted",
    "receipt.accepted",
    "plan.completed",
    "plan.approval_requested",
    "approve_plan",
    "plan.execute",
    "execution.acknowledged",
    "execution.progress",
    "execution.completed",
    "completion.review",
    "review.completed",
    "accept_completion",
)


def validate_gt003_fixtures(root: Path | None = None) -> list[str]:
    directory = root or EXAMPLES_GT003
    errors: list[str] = []
    if not directory.exists():
        return [f"Missing GT-003 directory: {directory}"]
    for name in REQUIRED_FILES:
        if not (directory / name).exists():
            errors.append(f"Missing GT-003 file: {name}")
    if errors:
        return errors
    ids = _load_json(directory / "fixture-ids.json")
    if not isinstance(ids, dict):
        return ["fixture-ids.json must be an object."]
    missing = [key for key in REQUIRED_ID_KEYS if key not in ids]
    if missing:
        errors.append(f"GT-003 fixture-ids.json missing {missing}")
    work_order_id = str(ids.get("work_order_id") or "")
    fingerprints = ids.get("fingerprints")
    if not isinstance(fingerprints, dict):
        errors.append("fixture-ids.json must include fingerprints.")
        fingerprints = {}
    parsed: dict[str, Any] = {}
    for name, expected_type in RESPONSE_FILES:
        text = (directory / name).read_text(encoding="utf-8")
        try:
            packet = parse_response_markdown(text)
        except (ValueError, TypeError) as exc:
            errors.append(f"{name} failed awr.response/v1 parse: {exc}")
            continue
        parsed[expected_type] = packet
        if packet.response_type.value != expected_type:
            errors.append(f"{name} response_type is {packet.response_type.value}.")
        if packet.work_order_id != work_order_id:
            errors.append(f"{name} work_order_id is inconsistent.")
        if packet.authority != "report_only":
            errors.append(f"{name} must be report_only.")
        digest = packet.content_sha256 or ""
        if digest != fingerprint_packet(packet):
            errors.append(f"{name} content_sha256 does not match fingerprint_packet.")
        expected_fp = fingerprints.get(expected_type)
        if expected_fp and expected_fp != digest:
            errors.append(f"{name} fingerprint does not match fixture-ids.json.")
    message_ids = ids.get("message_ids")
    if not isinstance(message_ids, dict):
        message_ids = {}
    if "receipt.accepted" in parsed and "plan.completed" in parsed:
        if parsed["plan.completed"].in_reply_to != message_ids.get("receipt"):
            errors.append("plan.completed in_reply_to must be the receipt message_id.")
        if parsed["receipt.accepted"].in_reply_to != message_ids.get("feature_plan"):
            errors.append("receipt.accepted in_reply_to must be the feature-plan message_id.")
        if parsed["plan.completed"].source_input_sha256 != ids.get("source_input_sha256"):
            errors.append("plan.completed source_input_sha256 must match the feature input.")
    if "execution.acknowledged" in parsed and parsed[
        "execution.acknowledged"
    ].executor_run_id != ids.get("executor_run_id"):
        errors.append("execution.acknowledged executor_run_id is inconsistent.")
    for later in ("execution.progress", "execution.completed"):
        if later in parsed and parsed[later].executor_run_id != ids.get("executor_run_id"):
            errors.append(f"{later} must repeat the acknowledged executor_run_id.")
    if (
        "plan.completed" in parsed
        and ids.get("plan_sha256") != parsed["plan.completed"].content_sha256
    ):
        errors.append("plan_sha256 must equal the plan.completed packet fingerprint.")
    errors.extend(_validate_decisions(directory, ids, parsed))
    timeline = _load_json(directory / "12-expected-timeline.json")
    if not isinstance(timeline, list) or len(timeline) < 12:
        errors.append("GT-003 expected timeline must list the ordered receipts.")
    else:
        errors.extend(_validate_timeline(timeline, work_order_id, parsed, ids))
    for path in directory.glob("*"):
        if path.suffix not in {".md", ".json"}:
            continue
        text = path.read_text(encoding="utf-8")
        if FRONTMATTER_SECRET_RE.search(text):
            errors.append(f"{path.name} contains a token-like value.")
        if UNSUPPORTED_CLAIM_RE.search(text):
            errors.append(f"{path.name} makes an unsupported delivery or MCP claim.")
        if "api.cursor.com" in text:
            errors.append(f"{path.name} contains a live cloud identifier.")
    return errors


def _validate_decisions(directory: Path, ids: dict[str, Any], parsed: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    approve = _load_json(directory / "04-plan-approval.decision.json")
    accept = _load_json(directory / "11-completion-acceptance.decision.json")
    decision_ids = ids.get("decision_ids")
    if not isinstance(decision_ids, dict):
        decision_ids = {}
    if approve.get("kind") != "approve_plan":
        errors.append("04-plan-approval.decision.json kind must be approve_plan.")
    if approve.get("work_order_id") != ids.get("work_order_id"):
        errors.append("approve_plan work_order_id is inconsistent.")
    if approve.get("plan_id") != ids.get("plan_id"):
        errors.append("approve_plan plan_id is inconsistent.")
    if approve.get("plan_sha256") != ids.get("plan_sha256"):
        errors.append("approve_plan plan_sha256 must match the plan packet fingerprint.")
    if approve.get("decision_id") != decision_ids.get("approve_plan"):
        errors.append("approve_plan decision_id does not match fixture-ids.json.")
    if accept.get("kind") != "accept_completion":
        errors.append("11-completion-acceptance.decision.json kind must be accept_completion.")
    if accept.get("work_order_id") != ids.get("work_order_id"):
        errors.append("accept_completion work_order_id is inconsistent.")
    if accept.get("decision_id") != decision_ids.get("accept_completion"):
        errors.append("accept_completion decision_id does not match fixture-ids.json.")
    completed = parsed.get("execution.completed")
    if completed is not None and accept.get("target_sha256") != completed.content_sha256:
        errors.append("accept_completion target_sha256 must match execution.completed.")
    return errors


def _validate_timeline(
    timeline: list[Any],
    work_order_id: str,
    parsed: dict[str, Any],
    ids: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    kinds = [str(item.get("kind")) for item in timeline if isinstance(item, dict)]
    if kinds != list(EXPECTED_KINDS):
        errors.append(f"GT-003 timeline kinds must be {list(EXPECTED_KINDS)}.")
    seen: set[str] = set()
    for item in timeline:
        if not isinstance(item, dict):
            errors.append("Each timeline row must be an object.")
            continue
        if item.get("work_order_id") != work_order_id:
            errors.append("GT-003 timeline work_order_id is inconsistent.")
            break
        event_id = str(item.get("id") or "")
        if not event_id:
            errors.append("Timeline rows require an id.")
            continue
        if event_id in seen:
            errors.append(f"Duplicate timeline id {event_id}.")
        seen.add(event_id)
        parent = item.get("in_reply_to")
        if parent and parent not in seen and parent != "msg_gt003_feature_plan":
            errors.append(f"Timeline parent {parent} is not a prior event.")
    fingerprints = ids.get("fingerprints")
    if isinstance(fingerprints, dict):
        for kind, digest in fingerprints.items():
            if kind in parsed and parsed[kind].content_sha256 != digest:
                errors.append(f"Timeline fingerprint for {kind} is stale.")
    return errors


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    errors = validate_gt003_fixtures()
    if errors:
        print("GT-003 validation failed:")
        for item in errors:
            print(f"- {item}")
        return 1
    print("GT-003 fixture validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
