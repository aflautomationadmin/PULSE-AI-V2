from __future__ import annotations

import re
from collections import deque
from datetime import datetime, timezone
from typing import Any, Literal

from src.memory import MemoryTurn, ThreadSummary, _ROUTES, _VALID_THREAD_ID

# ── MongoDB document structure ────────────────────────────────────────────────
#
# Collection: conversation_threads
# One document per (app_id, user_id, thread_id):
#
# {
#   "_id":        ObjectId("..."),
#   "thread_id":  "thread-20260417-143022",
#   "app_id":     "ai-da-agents",
#   "user_id":    "anonymous",
#   "created_at": ISODate("2026-04-17T14:30:00Z"),
#   "updated_at": ISODate("2026-04-17T14:35:10Z"),
#   "turns": [
#     {
#       "user":            "give ABV trend for last 10 days",
#       "assistant":       "The ABV over the last 10 days ranged from 4,278 to 6,744...",
#       "route":           "business_question",
#       "sql_used":        "EXEC GetABVai @date_from='2026-04-07', @date_to='2026-04-17', @time_grain='AUTO'",
#       "sql_explanation": "Availability metrics from 7th to 17th April, grouped by day.",
#       "citations":       [{"claim": "...", "source_column": "...", ...}],
#       "verification":    {"verified": true, "issues": []},
#       "chart_data":      {"chart_type": "line", "title": "...", "labels": [...], ...},
#       "chart_type":      "line",
#       "row_preview":     [{"Day": "07 Apr", "ABV": 6012.34}, ...],
#       "created_at":      ISODate("2026-04-17T14:30:22Z")
#     }
#   ]
# }
#
# Indexes:
#   UNIQUE  (app_id, user_id, thread_id)   → thread isolation per user
#   INDEX   (app_id, user_id, updated_at)  → fast per-user thread listing
# ─────────────────────────────────────────────────────────────────────────────

_APP_ID = "ai-da-agents"
_ANONYMOUS_USER = "anonymous"


class MongoConversationMemory:
    """
    MongoDB-backed conversation memory — full drop-in replacement for
    ConversationMemory.

    All threads are kept in memory for fast reads; every mutation is
    immediately persisted to MongoDB.  If MongoDB is unreachable a write
    fails silently — the in-memory state is always consistent.
    """

    def __init__(
        self,
        uri: str,
        db_name: str,
        collection: str,
        max_turns: int = 12,
        default_thread_id: str = "default",
        auto_create_thread: bool = True,
        user_id: str | None = None,
    ) -> None:
        try:
            from pymongo import MongoClient, ASCENDING  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "pymongo is required for MongoDB memory. Run: pip install pymongo"
            ) from exc

        self._max_turns = max(1, int(max_turns))
        self._user_id = (user_id or _ANONYMOUS_USER).strip() or _ANONYMOUS_USER
        self._client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        self._col = self._client[db_name][collection]

        # Ensure indexes exist (idempotent)
        self._col.create_index(
            [("app_id", ASCENDING), ("user_id", ASCENDING), ("thread_id", ASCENDING)],
            unique=True,
            background=True,
        )
        self._col.create_index(
            [("app_id", ASCENDING), ("user_id", ASCENDING), ("updated_at", ASCENDING)],
            background=True,
        )

        self._threads: dict[str, deque[MemoryTurn]] = {}
        self._active_thread_id = self._normalize_thread_id(default_thread_id)

        self._load_from_mongo()
        self._ensure_thread(self._active_thread_id)

        if auto_create_thread and self._active_thread_id not in self._threads:
            self._ensure_thread(self._active_thread_id)

        self._save_thread(self._active_thread_id)

    # ── Public API (mirrors ConversationMemory exactly) ───────────────────────

    def add_turn(
        self,
        *,
        user: str,
        assistant: str,
        route: Literal["business_question", "normal_chat"],
        sql_used: str | None = None,
        sql_explanation: str | None = None,
        citations: list[dict[str, Any]] | None = None,
        verification: dict[str, Any] | None = None,
        chart_data: dict[str, Any] | None = None,
        chart_type: str | None = None,
        row_preview: list[dict[str, Any]] | None = None,
        trace_id: str | None = None,
    ) -> None:
        self._current_turns().append(
            MemoryTurn(
                user=user.strip(),
                assistant=assistant.strip(),
                route=route,
                sql_used=sql_used,
                sql_explanation=sql_explanation,
                citations=citations or [],
                verification=verification,
                chart_data=chart_data,
                chart_type=chart_type,
                row_preview=row_preview,
                trace_id=trace_id,
            )
        )
        self._save_thread(self._active_thread_id)

    def clear(self) -> None:
        self._current_turns().clear()
        self._save_thread(self._active_thread_id)

    def turn_count(self) -> int:
        return len(self._current_turns())

    def is_empty(self) -> bool:
        return len(self._current_turns()) == 0

    def active_thread_id(self) -> str:
        return self._active_thread_id

    def get_thread_turns(self, thread_id: str) -> list[MemoryTurn]:
        """Return all turns for a thread without changing the active thread."""
        normalized = self._normalize_thread_id(thread_id)
        if normalized not in self._threads:
            raise ValueError(f"Thread '{normalized}' does not exist.")
        return list(self._threads[normalized])

    def get_last_business_turn(self) -> MemoryTurn | None:
        """Return the most recent business_question turn that has row_preview data."""
        for turn in reversed(list(self._current_turns())):
            if turn.route == "business_question" and turn.row_preview:
                return turn
        return None

    def list_threads(self) -> list[ThreadSummary]:
        ordered_ids = sorted(self._threads, reverse=True)
        return [
            ThreadSummary(
                thread_id=tid,
                turn_count=len(self._threads[tid]),
                is_active=(tid == self._active_thread_id),
                title=self._thread_title(tid),
            )
            for tid in ordered_ids
        ]

    def create_thread(self, thread_id: str, *, switch: bool = True) -> str:
        normalized = self._normalize_thread_id(thread_id)
        self._ensure_thread(normalized)
        if switch:
            self._active_thread_id = normalized
        self._save_thread(normalized)
        return normalized

    def switch_thread(self, thread_id: str, *, create_if_missing: bool = False) -> str:
        normalized = self._normalize_thread_id(thread_id)
        if normalized not in self._threads:
            if not create_if_missing:
                raise ValueError(f"Thread '{normalized}' does not exist.")
            self._ensure_thread(normalized)
            self._save_thread(normalized)
        self._active_thread_id = normalized
        return normalized

    def format_for_prompt(self) -> str:
        turns = self._current_turns()
        if not turns:
            return f"(no prior conversation in thread '{self._active_thread_id}')"
        lines: list[str] = []
        for idx, turn in enumerate(turns, start=1):
            lines.append(f"Turn {idx} | route={turn.route}")
            lines.append(f"User: {turn.user}")
            lines.append(f"Assistant: {turn.assistant}")
            if turn.sql_used:
                lines.append(f"SQL used: {turn.sql_used}")
            if turn.sql_explanation:
                lines.append(f"SQL meaning: {turn.sql_explanation}")
        return "\n".join(lines)

    def format_for_display(self) -> str:
        turns = self._current_turns()
        if not turns:
            return f"(memory empty in thread '{self._active_thread_id}')"
        lines: list[str] = [f"Active thread: {self._active_thread_id}"]
        for idx, turn in enumerate(turns, start=1):
            lines.append(f"{idx}. [{turn.route}]")
            lines.append(f"   user: {turn.user}")
            lines.append(f"   bot:  {turn.assistant}")
        return "\n".join(lines)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _current_turns(self) -> deque[MemoryTurn]:
        return self._threads[self._active_thread_id]

    def _ensure_thread(self, thread_id: str) -> None:
        if thread_id not in self._threads:
            self._threads[thread_id] = deque(maxlen=self._max_turns)

    def _thread_title(self, thread_id: str) -> str:
        turns = self._threads.get(thread_id)
        if turns:
            first_question = next((turn.user.strip() for turn in turns if turn.user.strip()), "")
            if first_question:
                return first_question[:57] + "..." if len(first_question) > 60 else first_question
        return "Default conversation" if thread_id == "default" else "New conversation"

    def _normalize_thread_id(self, thread_id: str) -> str:
        normalized = thread_id.strip()
        if not _VALID_THREAD_ID.fullmatch(normalized):
            raise ValueError(
                "Invalid thread id. Use 1-64 chars: letters, numbers, '.', '_' or '-'."
            )
        return normalized

    def _load_from_mongo(self) -> None:
        """Load all threads for this user from MongoDB into memory on startup."""
        try:
            docs = self._col.find({"app_id": _APP_ID, "user_id": self._user_id})
            for doc in docs:
                thread_id = doc.get("thread_id", "")
                if not _VALID_THREAD_ID.fullmatch(thread_id):
                    continue
                q: deque[MemoryTurn] = deque(maxlen=self._max_turns)
                for t in doc.get("turns", []):
                    user = t.get("user", "")
                    assistant = t.get("assistant", "")
                    route = t.get("route", "")
                    if not isinstance(user, str) or not isinstance(assistant, str):
                        continue
                    if route not in _ROUTES:
                        continue
                    q.append(MemoryTurn(
                        user=user,
                        assistant=assistant,
                        route=route,  # type: ignore[arg-type]
                        sql_used=t.get("sql_used"),
                        sql_explanation=t.get("sql_explanation"),
                        citations=t.get("citations") or [],
                        verification=t.get("verification"),
                        chart_data=t.get("chart_data"),
                        chart_type=t.get("chart_type"),
                        row_preview=t.get("row_preview"),
                        trace_id=t.get("trace_id"),
                    ))
                self._threads[thread_id] = q
        except Exception:
            pass   # Non-fatal — start with empty in-memory state

    def _save_thread(self, thread_id: str) -> None:
        """Upsert the thread document in MongoDB with all rich fields."""
        turns = self._threads.get(thread_id, deque())
        now = datetime.now(timezone.utc)

        serialized_turns = [
            {
                "user":            t.user,
                "assistant":       t.assistant,
                "route":           t.route,
                "sql_used":        t.sql_used,
                "sql_explanation": t.sql_explanation,
                "citations":       t.citations or [],
                "verification":    t.verification,
                "chart_data":      t.chart_data,
                "chart_type":      t.chart_type,
                "row_preview":     t.row_preview,
                "trace_id":        t.trace_id,
                "created_at":      now,
            }
            for t in turns
        ]

        try:
            self._col.update_one(
                {
                    "app_id":    _APP_ID,
                    "user_id":   self._user_id,
                    "thread_id": thread_id,
                },
                {
                    "$set": {
                        "turns":      serialized_turns,
                        "updated_at": now,
                    },
                    "$setOnInsert": {
                        "app_id":     _APP_ID,
                        "user_id":    self._user_id,
                        "thread_id":  thread_id,
                        "created_at": now,
                    },
                },
                upsert=True,
            )
        except Exception:
            pass   # Non-fatal — in-memory state stays consistent
