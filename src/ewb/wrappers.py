from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .contracts import Directive
from .decorators import strip_directive


@dataclass(frozen=True, slots=True)
class WrappedPrompt:
    wrapper_id: str
    wrapper_version: str
    wrapper_sha256: str
    markdown: str


_PLAN_POLICY = """# Engineering Work Broker envelope

- Operating mode: PLAN_ONLY
- Acknowledge the work-order ID before analysis.
- Review the request and repository context.
- Produce a concrete implementation plan and blocking questions.
- Do not edit files, run destructive commands, commit, push, or open a pull request.
- Return a structured plan packet to the broker.
"""


def wrap_prompt(directive: Directive, markdown: str, work_order_id: str) -> WrappedPrompt:
    wrapper_id = directive.name
    version = "1.0.0"
    policy_hash = hashlib.sha256(_PLAN_POLICY.encode("utf-8")).hexdigest()
    parent = directive.parent_work_order_id or "none"
    body = strip_directive(markdown)
    wrapped = (
        f"{_PLAN_POLICY}\n"
        f"Work-order ID: {work_order_id}\n"
        f"Intent: {directive.name}\n"
        f"Parent work order: {parent}\n"
        f"Wrapper: {wrapper_id}@{version}\n"
        f"Wrapper SHA-256: {policy_hash}\n\n"
        f"# Submitted work order\n\n{body}\n"
    )
    return WrappedPrompt(
        wrapper_id=wrapper_id,
        wrapper_version=version,
        wrapper_sha256=policy_hash,
        markdown=wrapped,
    )
