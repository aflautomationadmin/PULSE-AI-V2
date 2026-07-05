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
from datetime import datetime, timezone
from typing import Any

from src.config import get_settings

logger = logging.getLogger(__name__)

_APP_ID = "ai-da-agents"

# Module-level client cache — one MongoClient reused across admin requests.
_client: Any = None
_collection: Any = None
_roles_collection: Any = None


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


# ── Admin roles (DB-managed admins, layered on the ADMIN_EMAILS seed) ──────────

def _get_roles_collection() -> Any | None:
    """Return the admin_roles collection, or None if Mongo is not configured."""
    global _roles_collection
    settings = get_settings()
    if not settings.mongo_uri:
        return None
    if _roles_collection is not None:
        return _roles_collection
    # Ensure the shared client exists (also sets the module-level _client).
    if _get_collection() is None or _client is None:
        return None
    try:
        _roles_collection = _client[settings.mongo_db_name]["admin_roles"]
        _roles_collection.create_index("email", unique=True, background=True)
        return _roles_collection
    except Exception as exc:
        logger.warning("admin_store: failed to open admin_roles collection: %s", exc)
        return None


def list_admin_roles() -> list[dict[str, Any]]:
    """Return DB-managed admin entries (excludes the env-var seed admins)."""
    col = _get_roles_collection()
    if col is None:
        return []
    try:
        return [
            {
                "email": d.get("email", ""),
                "granted_by": d.get("granted_by"),
                "granted_at": _iso(d.get("granted_at")),
            }
            for d in col.find({})
        ]
    except Exception as exc:
        logger.warning("admin_store.list_admin_roles failed: %s", exc)
        return []


def add_admin_role(email: str, granted_by: str) -> bool:
    """Grant admin to an email (idempotent). Returns True on success."""
    col = _get_roles_collection()
    if col is None:
        return False
    email = email.lower().strip()
    try:
        col.update_one(
            {"email": email},
            {
                "$set": {"email": email, "granted_by": granted_by},
                "$setOnInsert": {"granted_at": datetime.now(timezone.utc)},
            },
            upsert=True,
        )
        return True
    except Exception as exc:
        logger.warning("admin_store.add_admin_role failed: %s", exc)
        return False


def remove_admin_role(email: str) -> bool:
    """Revoke a DB-managed admin. Returns True if a row was deleted."""
    col = _get_roles_collection()
    if col is None:
        return False
    try:
        result = col.delete_one({"email": email.lower().strip()})
        return result.deleted_count > 0
    except Exception as exc:
        logger.warning("admin_store.remove_admin_role failed: %s", exc)
        return False


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


def get_usage_summary() -> dict[str, Any]:
    """
    Aggregate per-agent token usage and cost across every stored turn.

    Returns:
        {
          "by_agent": [ {agent, calls, prompt_tokens, completion_tokens,
                         total_tokens, cost}, ... ]  (sorted by total_tokens desc),
          "by_user":  [ {user_id, total_tokens, cost, turns}, ... ],
          "totals":   {prompt_tokens, completion_tokens, total_tokens, cost,
                       turns, tracked_turns}
        }
    """
    col = _get_collection()
    empty = {"by_agent": [], "by_user": [], "totals": {
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        "cost": 0.0, "turns": 0, "tracked_turns": 0}}
    if col is None:
        return empty

    try:
        docs = col.find({"app_id": _APP_ID}, {"user_id": 1, "turns.token_usage": 1})
    except Exception as exc:
        logger.warning("admin_store.get_usage_summary failed: %s", exc)
        return empty

    by_agent: dict[str, dict[str, Any]] = {}
    by_user: dict[str, dict[str, Any]] = {}
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
              "cost": 0.0, "turns": 0, "tracked_turns": 0}

    for doc in docs:
        uid = doc.get("user_id") or "anonymous"
        user_entry = by_user.setdefault(
            uid, {"user_id": uid, "total_tokens": 0, "cost": 0.0, "turns": 0})
        for turn in doc.get("turns", []):
            totals["turns"] += 1
            usage = turn.get("token_usage") or {}
            if not usage:
                continue
            totals["tracked_turns"] += 1
            for agent, stats in usage.items():
                if not isinstance(stats, dict):
                    continue
                a = by_agent.setdefault(agent, {
                    "agent": agent, "calls": 0, "prompt_tokens": 0,
                    "completion_tokens": 0, "total_tokens": 0, "cost": 0.0})
                pt = int(stats.get("prompt_tokens", 0) or 0)
                ct = int(stats.get("completion_tokens", 0) or 0)
                tt = int(stats.get("total_tokens", 0) or 0)
                cost = float(stats.get("cost", 0.0) or 0.0)
                calls = int(stats.get("calls", 0) or 0)
                a["calls"] += calls
                a["prompt_tokens"] += pt
                a["completion_tokens"] += ct
                a["total_tokens"] += tt
                a["cost"] += cost
                totals["prompt_tokens"] += pt
                totals["completion_tokens"] += ct
                totals["total_tokens"] += tt
                totals["cost"] += cost
                user_entry["total_tokens"] += tt
                user_entry["cost"] += cost
            user_entry["turns"] += 1

    by_agent_list = sorted(by_agent.values(), key=lambda x: x["total_tokens"], reverse=True)
    by_user_list = sorted(by_user.values(), key=lambda x: x["total_tokens"], reverse=True)
    totals["cost"] = round(totals["cost"], 6)
    for row in by_agent_list:
        row["cost"] = round(row["cost"], 6)
    for row in by_user_list:
        row["cost"] = round(row["cost"], 6)
    return {"by_agent": by_agent_list, "by_user": by_user_list, "totals": totals}


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
