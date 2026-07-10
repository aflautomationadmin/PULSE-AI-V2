"""
Request tracing helpers.

Uses Python ContextVar to propagate a per-request trace_id + user_id across the
entire pipeline without changing any agent function signatures. The trace_id is
a stable identifier for one chat turn — stored on the turn and used to correlate
user feedback.
"""

from __future__ import annotations

import time
from contextvars import ContextVar

# ── Per-request context (thread-safe via ContextVar) ─────────────────────────
_trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)
_user_id_var:  ContextVar[str | None] = ContextVar("user_id",  default=None)
# perf_counter timestamp marking when the current request started
_request_start_var: ContextVar[float | None] = ContextVar("request_start", default=None)


def mark_request_start() -> None:
    """Record the start time of the current request (for response-time metrics)."""
    _request_start_var.set(time.perf_counter())


def get_elapsed_ms() -> int:
    """Milliseconds since mark_request_start() for this request (0 if unset)."""
    start = _request_start_var.get()
    if start is None:
        return 0
    return int((time.perf_counter() - start) * 1000)

# Per-request token accumulator: {agent_name: {prompt_tokens, completion_tokens,
# total_tokens, cost, calls}}. Reset at the start of each chat request; read by
# memory.add_turn() when a turn is persisted.
_token_usage_var: ContextVar[dict | None] = ContextVar("token_usage", default=None)


def reset_token_usage() -> None:
    """Start a fresh per-request token tally. Call once at request start."""
    _token_usage_var.set({})


def record_token_usage(
    agent_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    cost: float,
) -> None:
    """Accumulate one LLM call's token usage under its agent name."""
    acc = _token_usage_var.get()
    if acc is None:
        acc = {}
        _token_usage_var.set(acc)
    entry = acc.setdefault(
        agent_name,
        {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0.0, "calls": 0},
    )
    entry["prompt_tokens"] += int(prompt_tokens or 0)
    entry["completion_tokens"] += int(completion_tokens or 0)
    entry["total_tokens"] += int(total_tokens or 0)
    entry["cost"] += float(cost or 0.0)
    entry["calls"] += 1


def get_token_usage() -> dict:
    """Return a snapshot copy of the current request's per-agent token tally."""
    acc = _token_usage_var.get()
    if not acc:
        return {}
    return {agent: dict(stats) for agent, stats in acc.items()}


def set_trace_context(trace_id: str, user_id: str) -> None:
    """Called once per request to bind trace + user to the current thread/task."""
    _trace_id_var.set(trace_id)
    _user_id_var.set(user_id)


def current_trace_id() -> str | None:
    """Read by llm.py on every LiteLLM call to attach the generation to the trace."""
    return _trace_id_var.get()


def current_user_id() -> str | None:
    return _user_id_var.get()
