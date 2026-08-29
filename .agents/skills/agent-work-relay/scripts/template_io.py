"""Parse skill templates and compute exact SHA-256 fingerprints."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from skill_paths import INPUT_INTENTS, RESPONSE_TYPES

DECORATOR_RE = re.compile(r"^@(input|response)\s*$")
FRONTMATTER_SECRET_RE = re.compile(
    r"(?i)(sk-[a-z0-9]{10,}|ghp_[a-z0-9]{20,}|github_pat_[a-z0-9_]{20,}|"
    r"AKIA[0-9A-Z]{16}|Bearer\s+[A-Za-z0-9._\-]{20,}|xox[baprs]-)"
)
UNSUPPORTED_CLAIM_RE = re.compile(
    r"(?is)("
    r"executors? receive (?:clean )?(?:artifact )?bytes|"
    r"artifact bytes (?:are|will be) delivered|"
    r"cursor cloud (?:must|should) (?:call|open) (?:awr )?mcp|"
    r"direct outbound mcp is required for cursor|"
    r"ex-01 has merged|"
    r"lc-01 response tools are not implemented"
    r")"
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def parse_skill_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must start with YAML frontmatter.")
    close = text.find("\n---\n", 4)
    if close < 0:
        raise ValueError("SKILL.md frontmatter is not closed.")
    mapping: dict[str, str] = {}
    for raw in text[4:close].splitlines():
        if ":" not in raw or raw.startswith("#"):
            continue
        key, _, value = raw.partition(":")
        mapping[key.strip()] = value.strip().strip('"').strip("'")
    return mapping


def parse_template(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    first = next((line for line in lines if line.strip()), None)
    if first is None:
        raise ValueError(f"{path.name} is empty.")
    match = DECORATOR_RE.match(first)
    if match is None:
        raise ValueError(f"{path.name} must start with @input or @response.")
    decorator = f"@{match.group(1)}"
    marks = [line.strip() for line in lines if DECORATOR_RE.match(line.strip())]
    if len(marks) != 1:
        raise ValueError(f"{path.name} must contain exactly one top-level decorator.")
    try:
        open_idx = next(index for index, line in enumerate(lines) if line.strip() == "---")
        close_idx = next(
            index
            for index, line in enumerate(lines[open_idx + 1 :], start=open_idx + 1)
            if line.strip() == "---"
        )
    except StopIteration as exc:
        raise ValueError(f"{path.name} must include closed YAML frontmatter.") from exc
    envelope = _parse_awr_block("\n".join(lines[open_idx + 1 : close_idx]))
    return {
        "path": path,
        "text": text,
        "decorator": decorator,
        "envelope": envelope,
        "body": "\n".join(lines[close_idx + 1 :]),
    }


def _parse_awr_block(text: str) -> dict[str, str]:
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
        value = value.strip().strip('"').strip("'")
        if value in {"", "|", ">"}:
            continue
        mapping[key.strip()] = value
    if not mapping:
        raise ValueError("An envelope must contain an awr mapping.")
    return mapping


def template_direction(envelope: dict[str, str], decorator: str) -> str:
    schema = envelope.get("schema", "")
    if decorator == "@input":
        if schema != "awr.input/v1":
            raise ValueError("Input templates must use schema awr.input/v1.")
        intent = envelope.get("intent", "")
        if intent not in INPUT_INTENTS:
            raise ValueError(f"Unknown input intent: {intent}")
        return "input"
    if schema != "awr.response/v1":
        raise ValueError("Response templates must use schema awr.response/v1.")
    response_type = envelope.get("response_type", "")
    if response_type not in RESPONSE_TYPES:
        raise ValueError(f"Unknown response type: {response_type}")
    return "response"


def load_manifest(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError("Manifest must be an object.")
    return loaded


def markdown_links(text: str) -> list[str]:
    return re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)
