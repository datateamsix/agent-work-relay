from __future__ import annotations

import re

from .contracts import Directive, WorkAction, WorkKind


class DirectiveError(ValueError):
    """The work-order decorator is missing, ambiguous, or invalid."""


_DIRECTIVE = re.compile(
    r"^@awr\s+(?P<kind>feature|refinement)\.(?P<action>plan)"
    r"(?:\s+parent=(?P<parent>[A-Za-z0-9._:-]+))?$"
)


def parse_directive(markdown: str) -> Directive:
    lines = markdown.splitlines()
    if not lines:
        raise DirectiveError("The Markdown payload is empty.")

    decorated_lines = [line.strip() for line in lines if line.strip().startswith("@awr")]
    if len(decorated_lines) != 1 or lines[0].strip() != decorated_lines[0]:
        raise DirectiveError("Exactly one @awr directive must be the first line.")

    match = _DIRECTIVE.fullmatch(decorated_lines[0])
    if match is None:
        raise DirectiveError("Unknown or malformed @awr directive.")

    kind = WorkKind(match.group("kind"))
    action = WorkAction(match.group("action"))
    parent = match.group("parent")

    if kind is WorkKind.REFINEMENT and parent is None:
        raise DirectiveError("refinement.plan requires parent=<work-order-id>.")
    if kind is WorkKind.FEATURE and parent is not None:
        raise DirectiveError("feature.plan may not declare a parent work order.")

    return Directive(kind=kind, action=action, parent_work_order_id=parent)


def strip_directive(markdown: str) -> str:
    lines = markdown.splitlines()
    return "\n".join(lines[1:]).lstrip("\n")
