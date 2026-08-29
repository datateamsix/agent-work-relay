"""Deterministic AWR lifecycle kernel. Responses stay in awr.responses."""

from .decisions import (
    MAX_DECISION_RATIONALE_BYTES,
    DecisionTargetKind,
    DecisionType,
    StoredDecision,
    fingerprint_decision,
    require_rationale,
)
from .errors import (
    AuthorityError,
    IdempotencyConflict,
    LifecycleError,
    LineageError,
    TransitionError,
)
from .events import LifecycleEvent
from .kernel import (
    LifecycleSnapshot,
    TransitionResult,
    apply_broker_event,
    apply_decision,
    apply_response,
)
from .pending import pending_actions
from .transitions import TRANSITION_TABLE, allowed_events, next_state

__all__ = [
    "MAX_DECISION_RATIONALE_BYTES",
    "TRANSITION_TABLE",
    "AuthorityError",
    "DecisionTargetKind",
    "DecisionType",
    "IdempotencyConflict",
    "LifecycleError",
    "LifecycleEvent",
    "LifecycleSnapshot",
    "LineageError",
    "StoredDecision",
    "TransitionError",
    "TransitionResult",
    "allowed_events",
    "apply_broker_event",
    "apply_decision",
    "apply_response",
    "fingerprint_decision",
    "next_state",
    "pending_actions",
    "require_rationale",
]
