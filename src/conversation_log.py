"""
conversation_log.py
────────────────────
Logs every chat turn to the SQL table `dbo.PulseAI_Feedback` (configurable via
FEEDBACK_TABLE): the question, the answer, how long it took, tokens used, and —
once the user rates the answer — the like/dislike + comment.

  • log_turn(...)        fire-and-forget INSERT (runs in a background thread so
                         it never slows the chat response).
  • save_feedback(...)   synchronous UPDATE of the matching turn row by trace_id
                         (falls back to INSERT if the turn wasn't logged).

Every DB error is swallowed/logged — logging must never break a chat.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from src.config import get_settings
from src.db.connection import get_connection

logger = logging.getLogger(__name__)

# Small pool so turn logging runs off the request path without connection storms.
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="convlog")

_COLUMNS = (
    "id, created_at, user_email, thread_id, trace_id, route, "
    "question, response, response_time_ms, total_tokens, liked, comment"
)


def _truncate(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit]
    return value


def log_turn(
    *,
    question: str,
    answer: str,
    route: str,
    user_email: str | None,
    thread_id: str | None,
    trace_id: str | None,
    response_time_ms: int,
    total_tokens: int,
) -> None:
    """Queue a background INSERT of one chat turn (no feedback yet)."""
    row = {
        "id": str(uuid4()),
        "created_at": datetime.now(timezone.utc),
        "user_email": _truncate(user_email, 256),
        "thread_id": _truncate(thread_id, 64),
        "trace_id": _truncate(trace_id, 64),
        "route": _truncate(route, 32),
        "question": _truncate(question, 4000),
        "response": _truncate(answer, 8000),
        "response_time_ms": int(response_time_ms or 0),
        "total_tokens": int(total_tokens or 0),
        "liked": None,
        "comment": None,
    }
    try:
        _executor.submit(_insert_row, row)
    except Exception as exc:  # pool shutdown, etc.
        logger.warning("conversation_log: could not queue turn: %s", exc)


def log_turn_for_memory(
    memory: Any,
    question: str,
    answer: str,
    route: str,
    token_usage: dict[str, Any] | None = None,
) -> None:
    """
    Called from memory.add_turn. Reads the turn's trace_id, start time and user
    from the memory INSTANCE (set via begin_turn) rather than context vars —
    instance attributes survive SSE streaming, context vars do not.
    """
    try:
        import time
        from src.tracing import get_token_usage

        start = getattr(memory, "_log_turn_start", None)
        rt_ms = int((time.perf_counter() - start) * 1000) if start else 0
        usage = token_usage if token_usage is not None else get_token_usage()
        total = sum(int((v or {}).get("total_tokens", 0) or 0) for v in (usage or {}).values())
        log_turn(
            question=question,
            answer=answer,
            route=route,
            user_email=getattr(memory, "_user_id", None),
            thread_id=getattr(memory, "_active_thread_id", None),
            trace_id=getattr(memory, "_log_trace_id", None),
            response_time_ms=rt_ms,
            total_tokens=total,
        )
    except Exception as exc:
        logger.warning("conversation_log: log_turn_for_memory failed: %s", exc)


def _insert_row(row: dict[str, Any]) -> None:
    table = get_settings().feedback_table
    sql = (
        f"INSERT INTO {table} ({_COLUMNS}) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    try:
        with closing(get_connection()) as conn:
            cur = conn.cursor()
            cur.execute(sql, [
                row["id"], row["created_at"], row["user_email"], row["thread_id"],
                row["trace_id"], row["route"], row["question"], row["response"],
                row["response_time_ms"], row["total_tokens"], row["liked"], row["comment"],
            ])
            conn.commit()
    except Exception as exc:
        logger.warning("conversation_log: turn INSERT failed: %s", exc)


def save_feedback(
    trace_id: str,
    user_email: str | None,
    liked: str,
    comment: str | None,
    response: str | None = None,
    question: str | None = None,
) -> bool:
    """
    Attach a 👍/👎 rating to the logged turn (UPDATE by trace_id). If the turn
    row does not exist yet, insert a minimal row so feedback is never lost.
    Returns True on success.
    """
    table = get_settings().feedback_table
    comment = _truncate(comment, 4000)
    try:
        with closing(get_connection()) as conn:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE {table} SET liked = ?, comment = ? WHERE trace_id = ?",
                [liked, comment, trace_id],
            )
            updated = cur.rowcount
            if not updated:
                # No logged turn for this trace_id — insert what we have.
                cur.execute(
                    f"INSERT INTO {table} ({_COLUMNS}) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        str(uuid4()), datetime.now(timezone.utc),
                        _truncate(user_email, 256), None, _truncate(trace_id, 64),
                        None, _truncate(question, 4000), _truncate(response, 8000),
                        None, None, liked, comment,
                    ],
                )
            conn.commit()
        return True
    except Exception as exc:
        logger.warning("conversation_log: save_feedback failed: %s", exc)
        return False
