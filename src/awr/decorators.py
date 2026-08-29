from __future__ import annotations

import re

from .contracts import Directive, WorkAction, WorkKind


class DirectiveError(ValueError):
    """The work-order decorator is missing, ambiguous, or invalid."""


_DIRECTIVE = re.compile(
    r"^@awr\s+(?P<kind>feature|refinement)\.(?P<action>plan)"
    r"(?:\s+parent=(?P<parent>[A-Za-z0-9._:-]+))?$"
)
_PLAN_INTENTS = frozenset({"feature.plan", "refinement.plan"})
_TRUTHY = frozenset({"true", "True", "yes", "1"})


def parse_directive(markdown: str) -> Directive:
    lines = markdown.splitlines()
    if not lines:
        raise DirectiveError("The Markdown payload is empty.")

    first_nonblank = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first_nonblank is None:
        raise DirectiveError("The Markdown payload is empty.")
    heading = lines[first_nonblank].strip()
    if heading == "@input":
        return _parse_input_directive(lines, first_nonblank)

    decorated_lines = [line.strip() for line in lines if line.strip().startswith("@awr")]
    if len(decorated_lines) != 1 or lines[0].strip() != decorated_lines[0]:
        raise DirectiveError("Exactly one @awr directive must be the first line.")

    match = _DIRECTIVE.fullmatch(decorated_lines[0])
    if match is None:
        raise DirectiveError("Unknown or malformed @awr directive.")

    kind = WorkKind(match.group("kind"))
    action = WorkAction(match.group("action"))
    parent = match.group("parent")
    return _bound_plan_directive(kind, action, parent, form="awr_alias")


def strip_directive(markdown: str) -> str:
    lines = markdown.splitlines()
    first_nonblank = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first_nonblank is not None and lines[first_nonblank].strip() == "@input":
        closing = _frontmatter_close_index(lines, first_nonblank)
        return "\n".join(lines[closing + 1 :]).lstrip("\n")
    return "\n".join(lines[1:]).lstrip("\n")


def _parse_input_directive(lines: list[str], start: int) -> Directive:
    input_marks = [line.strip() for line in lines if line.strip() == "@input"]
    if len(input_marks) != 1:
        raise DirectiveError("Exactly one @input decorator must lead the document.")
    close = _frontmatter_close_index(lines, start)
    open_index = _frontmatter_open_index(lines, start)
    mapping = _parse_awr_mapping("\n".join(lines[open_index + 1 : close]))
    intent = mapping.get("intent") or mapping.get("message_type")
    if (
        mapping.get("intent")
        and mapping.get("message_type")
        and mapping["intent"] != mapping["message_type"]
    ):
        raise DirectiveError("intent and message_type disagree.")
    if intent is None:
        raise DirectiveError("An @input envelope must declare intent or message_type.")
    if intent not in _PLAN_INTENTS:
        raise DirectiveError("Unknown or unsupported @input planning intent.")
    if mapping.get("execution_authorized") in _TRUTHY:
        raise DirectiveError("Execution is not authorized on this control plane.")
    authority = mapping.get("requested_authority") or mapping.get("requested_action")
    if authority is not None and authority not in {"plan_only", "plan"}:
        raise DirectiveError("Only plan_only authority is accepted for planning inputs.")
    kind_name, action_name = intent.split(".")
    parent = mapping.get("parent_work_order_id")
    if parent in {None, "", "null", "~", "None"}:
        parent = None
    return _bound_plan_directive(WorkKind(kind_name), WorkAction(action_name), parent, form="input")


def _bound_plan_directive(
    kind: WorkKind, action: WorkAction, parent: str | None, *, form: str
) -> Directive:
    if action is not WorkAction.PLAN:
        raise DirectiveError("Unknown or malformed planning directive.")
    if kind is WorkKind.REFINEMENT and parent is None:
        raise DirectiveError("refinement.plan requires parent=<work-order-id>.")
    if kind is WorkKind.FEATURE and parent is not None:
        raise DirectiveError("feature.plan may not declare a parent work order.")
    return Directive(kind=kind, action=action, parent_work_order_id=parent, form=form)


def _frontmatter_open_index(lines: list[str], start: int) -> int:
    for index in range(start + 1, len(lines)):
        if not lines[index].strip():
            continue
        if lines[index].strip() == "---":
            return index
        raise DirectiveError("An @input document must open YAML frontmatter after the decorator.")
    raise DirectiveError("An @input document must include YAML frontmatter.")


def _frontmatter_close_index(lines: list[str], start: int) -> int:
    open_index = _frontmatter_open_index(lines, start)
    for index in range(open_index + 1, len(lines)):
        if lines[index].strip() == "---":
            return index
    raise DirectiveError("An @input document must close YAML frontmatter.")


def _parse_awr_mapping(text: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    in_awr = False
    awr_indent = 0
    for raw in text.splitlines():
        if not in_awr:
            stripped = raw.lstrip()
            if stripped == "awr:" or stripped.startswith("awr:"):
                in_awr = True
                awr_indent = len(raw) - len(stripped)
            continue
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent <= awr_indent:
            break
        stripped = raw.strip()
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if value in {"", "|", ">"}:
            continue
        mapping[key] = value
    if not mapping:
        raise DirectiveError("An @input envelope must contain an awr mapping.")
    return mapping
