"""Request-scoped structural metadata for the current Discord interaction."""

from contextvars import ContextVar


CURRENT_INTERACTION_CONTEXT: ContextVar[dict | None] = ContextVar(
    "current_interaction_context",
    default=None,
)


__all__ = ["CURRENT_INTERACTION_CONTEXT"]
