from __future__ import annotations

import re
from collections import deque
from datetime import datetime, timezone
from typing import Literal

from src.memory import MemoryTurn, ThreadSummary, _ROUTES, _VALID_THREAD_ID

# ── MongoDB document structure ────────────────────────────────────────────────
#
# Collection: conversation_threads
# One document per thread:
# {
#   "thread_id":  "conv-20260406-abc123",   ← unique per user
#   "app_id":     "ai-da-agents",           ← namespaces threads per app
#   "user_id":    "user@arvind.com",        ← identifies the user (anonymous by default)
#   "turns": [
#     {
#       "user":       "show me USPA sales",
#       "assistant":  "USPA had ₹12L...",
#       "route":      "business_question",
#       "created_at": ISODate("2026-04-06T10:00:00Z")
#     }
#   ],
#   "created_at": ISODate("2026-04-06T10:00:00Z"),
#   "updated_at": ISODate("2026-04-06T10:05:00Z")
# }
#
# Unique index: (app_id, user_id, thread_id)
# → Each user has their own isolated thread namespace.
# → Querying all threads for a user: find({"app_id": ..., "user_id": ...})
# ─────────────────────────────────────────────────────────────────────────────

_APP_ID = "ai-da-agents"
_ANONYMOUS_USER = "anonymous"


class MongoConversationMemory:
    """
    MongoDB-backed conversation memory.

    Identical public interface to ConversationMemory so it is a drop-in
    replacement. All threads are kept in memory for fast reads; every
    mutation is immediately persisted to MongoDB.
    """

    def __init__(
        self,
        uri: str,
        db_name: str,
        collection: str,
        max_turns: int = 12,
        default_thread_id: str = "default",
        auto_create_thread: bool = True,
        user_id: str | None = None,          # None → "anonymous" (ready for auth)
    ) -> None:
        try:
            from pymongo import MongoClient, ASCENDING  # type: ignore
            from pymongo.errors import ConnectionFailure  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "pymongo is required for MongoDB memory. Run: pip install pymongo"
            ) from exc

        self._max_turns = max(1, int(max_turns))
        self._user_id = (user_id or _ANONYMOUS_USER).strip() or _ANONYMOUS_USER
        self._client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        self._col = self._client[db_name][collection]

        # Unique index on (app_id, user_id, thread_id)
        # → isolates each user's threads; supports per-user queries in future
        self._col.create_index(
            [("app_id", ASCENDING), ("user_id", ASCENDING), ("thread_id", ASCENDING)],
            unique=True,
            background=True,
        )
        # Index for fast per-user thread listing
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

    # ── Public API (mirrors ConversationMemory) ───────────────────────────────

    def add_turn(
        self,
        *,
        user: str,
        assistant: str,
        route: Literal["business_question", "normal_chat"],
    ) -> None:
        self._current_turns().append(
            MemoryTurn(user=user.strip(), assistant=assistant.strip(), route=route)
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

    def list_threads(self) -> list[ThreadSummary]:
        ordered_ids = [self._active_thread_id] + sorted(
            tid for tid in self._threads if tid != self._active_thread_id
        )
        return [
            ThreadSummary(
                thread_id=tid,
                turn_count=len(self._threads[tid]),
                is_active=(tid == self._active_thread_id),
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

    def _normalize_thread_id(self, thread_id: str) -> str:
        normalized = thread_id.strip()
        if not _VALID_THREAD_ID.fullmatch(normalized):
            raise ValueError(
                "Invalid thread id. Use 1-64 chars: letters, numbers, '.', '_' or '-'."
            )
        return normalized

    def _load_from_mongo(self) -> None:
        """Load all threads for this user from MongoDB into memory."""
        try:
            docs = self._col.find({"app_id": _APP_ID, "user_id": self._user_id})
            for doc in docs:
                thread_id = doc.get("thread_id", "")
                if not _VALID_THREAD_ID.fullmatch(thread_id):
                    continue
                raw_turns = doc.get("turns", [])
                q: deque[MemoryTurn] = deque(maxlen=self._max_turns)
                for t in raw_turns:
                    user = t.get("user", "")
                    assistant = t.get("assistant", "")
                    route = t.get("route", "")
                    if not isinstance(user, str) or not isinstance(assistant, str):
                        continue
                    if route not in _ROUTES:
                        continue
                    q.append(MemoryTurn(user=user, assistant=assistant, route=route))  # type: ignore
                self._threads[thread_id] = q
        except Exception:
            # Non-fatal: start with empty in-memory state if Mongo unreachable
            pass

    def _save_thread(self, thread_id: str) -> None:
        """Upsert a single thread document in MongoDB."""
        turns = self._threads.get(thread_id, deque())
        now = datetime.now(timezone.utc)
        serialized_turns = [
            {
                "user": t.user,
                "assistant": t.assistant,
                "route": t.route,
                "created_at": now,
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
            # Non-fatal: in-memory state is always consistent even if Mongo write fails
            pass
