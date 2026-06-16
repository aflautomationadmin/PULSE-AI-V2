"""
admin_store.py
──────────────
Read-only access to ALL users' conversations for the admin portal.

The per-user MongoConversationMemory is scoped to a single user_id; the admin
portal needs to read across every user.  This module talks to the same
MongoDB collection directly (read-only) to:

  • list every user who has ever chatted (with activity stats)
  • return the full conversation history for one user (all threads / turns)

If MongoDB is not configured, every function returns empty results so the
endpoints degrade gracefully instead of erroring.
"""
from __future__ import annotations

import logging
from typing import Any

from src.config import get_settings

logger = logging.getLogger(__name__)

_APP_ID = "ai-da-agents"

# Module-level client cache — one MongoClient reused across admin requests.
_client: Any = None
_collection: Any = None


def _get_collection() -> Any | None:
    """Return the conversation collection, or None if Mongo is not configured."""
    global _client, _collection
    settings = get_settings()
    if not settings.mongo_uri:
        return None
    if _collection is not None:
        return _collection
    try:
        from pymongo import MongoClient  # type: ignore
    except ImportError:
        logger.warning("pymongo not installed — admin portal cannot read conversations")
        return None
    try:
        _client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)
        _collection = _client[settings.mongo_db_name][settings.mongo_collection]
        return _collection
    except Exception as exc:
        logger.warning("admin_store: failed to connect to MongoDB: %s", exc)
        return None


def list_users() -> list[dict[str, Any]]:
    """
    Return one entry per user who has any conversation, sorted by most recent
    activity first:
        {user_id, thread_count, turn_count, last_activity}
    """
    col = _get_collection()
    if col is None:
        return []

    pipeline = [
        {"$match": {"app_id": _APP_ID}},
        {
            "$group": {
                "_id": "$user_id",
                "thread_count": {"$sum": 1},
                "turn_count": {"$sum": {"$size": {"$ifNull": ["$turns", []]}}},
                "last_activity": {"$max": "$updated_at"},
            }
        },
        {"$sort": {"last_activity": -1}},
    ]
    try:
        rows = list(col.aggregate(pipeline))
    except Exception as exc:
        logger.warning("admin_store.list_users failed: %s", exc)
        return []

    return [
        {
            "user_id": r.get("_id") or "anonymous",
            "thread_count": r.get("thread_count", 0),
            "turn_count": r.get("turn_count", 0),
            "last_activity": _iso(r.get("last_activity")),
        }
        for r in rows
    ]


def get_user_conversations(user_id: str) -> list[dict[str, Any]]:
    """
    Return every thread for one user, newest thread first, each with its full
    list of turns (question + answer + sql + route + timestamp).
    """
    col = _get_collection()
    if col is None:
        return []

    try:
        docs = col.find({"app_id": _APP_ID, "user_id": user_id}).sort("updated_at", -1)
    except Exception as exc:
        logger.warning("admin_store.get_user_conversations failed: %s", exc)
        return []

    threads: list[dict[str, Any]] = []
    for doc in docs:
        turns = []
        for t in doc.get("turns", []):
            turns.append(
                {
                    "user": t.get("user", ""),
                    "assistant": t.get("assistant", ""),
                    "route": t.get("route", ""),
                    "sql_used": t.get("sql_used"),
                    "sql_explanation": t.get("sql_explanation"),
                    "created_at": _iso(t.get("created_at")),
                }
            )
        threads.append(
            {
                "thread_id": doc.get("thread_id", ""),
                "created_at": _iso(doc.get("created_at")),
                "updated_at": _iso(doc.get("updated_at")),
                "turn_count": len(turns),
                "turns": turns,
            }
        )
    return threads


def _iso(value: Any) -> str | None:
    """Convert a datetime to an ISO string; pass through strings; else None."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)
