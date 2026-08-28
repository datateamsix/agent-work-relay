from __future__ import annotations


class ArtifactError(ValueError):
    """An artifact operation cannot proceed without violating a broker invariant."""


class ArtifactTooLargeError(ArtifactError):
    """Inbound bytes exceeded the configured artifact size limit."""


class ArtifactImmutabilityError(ArtifactError):
    """An existing immutable artifact body would have been overwritten."""


class ArtifactAccessError(ArtifactError):
    """A body was requested from a storage area that does not contain it."""


class ArtifactConflictError(ArtifactError):
    """Another worker holds a live scan lease for this artifact."""
