from __future__ import annotations


class LifecycleError(ValueError):
    """A lifecycle event, decision, or authority check failed closed."""


class TransitionError(LifecycleError):
    """The requested state transition is not permitted."""


class AuthorityError(LifecycleError):
    """Stored authority is missing, mismatched, or granted by a response."""


class IdempotencyConflict(LifecycleError):
    """An idempotency key is rebound to a different canonical packet."""


class LineageError(LifecycleError):
    """Parent, work-order, fingerprint, or provider-run binding is invalid."""
