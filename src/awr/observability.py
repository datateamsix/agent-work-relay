from __future__ import annotations

import json
import logging
import re
from typing import Any

_SECRET_KEYS = {
    "authorization",
    "access_token",
    "refresh_token",
    "id_token",
    "cursor_api_key",
    "api_key",
    "static_token",
    "password",
    "secret",
    "client_secret",
}
_REDACT_PATTERN = re.compile(
    r"(authorization|bearer|api[_-]?key|cursor_api_key|secret|token)\s*[:=]\s*\S+",
    re.IGNORECASE,
)


class RedactingFilter(logging.Filter):
    """Drop raw prompts, plan bodies, tokens, and secret-bearing fields."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_text(str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = {key: _redact_value(key, value) for key, value in record.args.items()}
            else:
                record.args = tuple(_redact_text(str(arg)) for arg in record.args)
        return True


def _redact_text(value: str) -> str:
    return _REDACT_PATTERN.sub(r"\1=[REDACTED]", value)


def _redact_value(key: str, value: Any) -> Any:
    if key.lower() in _SECRET_KEYS:
        return "[REDACTED]"
    if key.lower() in {"markdown", "content", "wrapped_markdown", "plan", "prompt"}:
        return "[REDACTED]"
    if isinstance(value, str):
        return _redact_text(value)
    return value


def configure_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("awr")
    if logger.handlers:
        logger.setLevel(level.upper())
        return logger
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False
    return logger


def log_event(logger: logging.Logger, event_type: str, **fields: Any) -> None:
    payload = {"event_type": event_type, **fields}
    safe = {key: _redact_value(key, value) for key, value in payload.items()}
    logger.info(json.dumps(safe, default=str, sort_keys=True))
