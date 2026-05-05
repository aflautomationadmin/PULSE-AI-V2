"""
Langfuse tracing helpers.

Uses Python ContextVar to propagate trace_id + user_id across the entire
request pipeline without changing any agent function signatures.

Usage pattern in the orchestrator:
    lf = get_langfuse()
    if lf:
        trace = lf.trace(name="chat_request", user_id=..., input=question)
        set_trace_context(trace.id, user_id)

Usage pattern in llm.py (called automatically via _lf_meta):
    metadata = _lf_meta("sql_writer")  # reads ContextVar internally
"""

from __future__ import annotations

from contextvars import ContextVar
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langfuse import Langfuse

# ── Per-request context (thread-safe via ContextVar) ─────────────────────────
_trace_id_var: ContextVar[str | None] = ContextVar("langfuse_trace_id", default=None)
_user_id_var:  ContextVar[str | None] = ContextVar("langfuse_user_id",  default=None)


@lru_cache(maxsize=1)
def get_langfuse() -> "Langfuse | None":
    """
    Return a Langfuse client singleton, or None if not configured.
    Graceful degradation: the app works normally when keys are absent.
    """
    from src.config import get_settings
    s = get_settings()
    if not s.langfuse_secret_key:
        return None
    try:
        from langfuse import Langfuse
        return Langfuse(
            public_key=s.langfuse_public_key,
            secret_key=s.langfuse_secret_key,
            host=s.langfuse_host,
        )
    except Exception:
        return None


def set_trace_context(trace_id: str, user_id: str) -> None:
    """Called once per request to bind trace + user to the current thread/task."""
    _trace_id_var.set(trace_id)
    _user_id_var.set(user_id)


def current_trace_id() -> str | None:
    """Read by llm.py on every LiteLLM call to attach the generation to the trace."""
    return _trace_id_var.get()


def current_user_id() -> str | None:
    return _user_id_var.get()
