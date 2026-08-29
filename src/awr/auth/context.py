from __future__ import annotations

from contextvars import ContextVar

from .tokens import Principal

_CURRENT_PRINCIPAL: ContextVar[Principal | None] = ContextVar("awr_principal", default=None)


def set_current_principal(principal: Principal | None) -> object:
    return _CURRENT_PRINCIPAL.set(principal)


def reset_current_principal(token: object) -> None:
    _CURRENT_PRINCIPAL.reset(token)  # type: ignore[arg-type]


def current_principal() -> Principal | None:
    return _CURRENT_PRINCIPAL.get()


def resolve_actor(explicit: str | None) -> str:
    principal = current_principal()
    if principal is not None:
        return principal.subject
    if explicit:
        return explicit
    raise ValueError("An authenticated actor or sender is required.")
