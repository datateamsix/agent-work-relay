"""Deterministic AWR lifecycle kernel. Responses stay in awr.responses."""

from .decisions import DecisionTargetKind, DecisionType, StoredDecision
from .errors import AuthorityError, IdempotencyConflict, LifecycleError, LineageError, TransitionError
from .events import LifecycleEvent
from .kernel import LifecycleSnapshot, TransitionResult, apply_broker_event, apply_decision, apply_response
from .pending import pending_actions
from .transitions import TRANSITION_TABLE, allowed_events, next_state

__all__ = [
    "AuthorityError",
    "DecisionTargetKind",
    "DecisionType",
    "IdempotencyConflict",
    "LifecycleError",
    "LifecycleEvent",
    "LifecycleSnapshot",
    "LineageError",
    "StoredDecision",
    "TRANSITION_TABLE",
    "TransitionError",
    "TransitionResult",
    "allowed_events",
    "apply_broker_event",
    "apply_decision",
    "apply_response",
    "next_state",
    "pending_actions",
]
