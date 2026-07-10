from __future__ import annotations

import json
import logging
import re
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.admin_store import (
    add_admin_role,
    get_usage_summary,
    get_user_conversations,
    list_admin_roles,
    list_users,
    remove_admin_role,
)
from src.auth import get_current_user, is_admin, is_seed_admin, require_admin
from src.business_context import format_context_for_prompt
from src.config import get_settings
from src.conversation_log import save_feedback
from src.orchestrator import ChatOrchestrator

logger = logging.getLogger(__name__)


# ── Per-user orchestrator registry ────────────────────────────────────────────
# One ChatOrchestrator per authenticated user, keyed by email.
# Created lazily on first request; kept alive for the process lifetime.
_orchestrators: dict[str, ChatOrchestrator] = {}
_lock = threading.Lock()


def _get_orchestrator(user_email: str) -> ChatOrchestrator:
    """Return (or lazily create) the orchestrator for this user."""
    if user_email not in _orchestrators:
        with _lock:
            if user_email not in _orchestrators:
                _orchestrators[user_email] = ChatOrchestrator(user_id=user_email)
    return _orchestrators[user_email]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # No eager warm-up needed; orchestrators are created on first request.
    yield


app = FastAPI(title="AI-DA-Agents API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request models ─────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None


class ThreadCreateRequest(BaseModel):
    thread_id: str


class FeedbackRequest(BaseModel):
    trace_id: str | None = None
    score: int          # 1 = 👍  0 = 👎
    comment: str | None = None
    thread_id: str | None = None
    response: str | None = None   # the bot answer that was rated
    question: str | None = None   # the user question (optional context)


# ── Chat (non-streaming) ───────────────────────────────────────────────────────

@app.post("/chat")
def chat(
    req: ChatRequest,
    user_email: str = Depends(get_current_user),
) -> dict[str, Any]:
    orch = _get_orchestrator(user_email)
    if req.thread_id:
        try:
            orch.switch_thread(req.thread_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
    try:
        reply = orch.handle_user_message(req.message)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    data = reply.model_dump()
    data["last_sql"] = orch.last_sql
    data["last_entity_match"] = orch.last_entity_match
    data["last_resolver_explanation"] = orch.last_resolver_explanation
    return data


# ── Chat (streaming SSE) ───────────────────────────────────────────────────────

@app.post("/chat/stream")
def chat_stream(
    req: ChatRequest,
    user_email: str = Depends(get_current_user),
) -> StreamingResponse:
    orch = _get_orchestrator(user_email)
    if req.thread_id:
        try:
            orch.switch_thread(req.thread_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    def event_generator():
        try:
            for event in orch.stream_handle_user_message(req.message):
                trace_id = event.get("trace_id") or orch.last_trace_id
                if trace_id and event.get("type") in {"complete", "metadata", "error"}:
                    event["trace_id"] = trace_id
                    logger.info(
                        "Chat stream event includes trace_id=%s type=%s thread_id=%s user=%s",
                        trace_id,
                        event.get("type"),
                        orch.active_thread(),
                        user_email,
                    )
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'content': str(exc)})}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Schema ─────────────────────────────────────────────────────────────────────

@app.post("/schema/refresh")
def refresh_schema(user_email: str = Depends(get_current_user)) -> dict[str, Any]:
    orch = _get_orchestrator(user_email)
    try:
        schema = orch.refresh_schema()
        return {"ok": True, "length": len(schema)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Memory ─────────────────────────────────────────────────────────────────────

@app.get("/memory")
def show_memory(user_email: str = Depends(get_current_user)) -> dict[str, Any]:
    return {"memory": _get_orchestrator(user_email).show_memory()}


@app.delete("/memory")
def clear_memory(user_email: str = Depends(get_current_user)) -> dict[str, Any]:
    _get_orchestrator(user_email).clear_memory()
    return {"ok": True}


# ── Threads ────────────────────────────────────────────────────────────────────

@app.get("/threads")
def list_threads(user_email: str = Depends(get_current_user)) -> dict[str, Any]:
    orch = _get_orchestrator(user_email)
    threads = orch.list_threads()
    return {"threads": [asdict(t) for t in threads], "active": orch.active_thread()}


@app.post("/threads")
def create_thread(
    req: ThreadCreateRequest,
    user_email: str = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return {"active": _get_orchestrator(user_email).create_thread(req.thread_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.put("/threads/{thread_id}/switch")
def switch_thread(
    thread_id: str,
    user_email: str = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return {"active": _get_orchestrator(user_email).switch_thread(thread_id)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/threads/{thread_id}/messages")
def get_thread_messages(
    thread_id: str,
    user_email: str = Depends(get_current_user),
) -> dict[str, Any]:
    orch = _get_orchestrator(user_email)
    try:
        messages = orch.get_thread_messages(thread_id)
        return {"messages": messages}
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ── SQL Cache ──────────────────────────────────────────────────────────────────

@app.get("/cache/sql")
def sql_cache_entries(
    limit: int = 10,
    user_email: str = Depends(get_current_user),
) -> dict[str, Any]:
    return {"entries": _get_orchestrator(user_email).list_sql_cache_entries(limit=limit)}


@app.delete("/cache/sql")
def clear_sql_cache(user_email: str = Depends(get_current_user)) -> dict[str, Any]:
    return {"cleared": _get_orchestrator(user_email).clear_sql_cache()}


# ── Context ────────────────────────────────────────────────────────────────────

@app.get("/context")
def show_context(user_email: str = Depends(get_current_user)) -> dict[str, Any]:
    orch = _get_orchestrator(user_email)
    context = orch.context_store.get_context()
    return {"context": format_context_for_prompt(context)}


# ── Feedback (stored in the SQL feedback table) ─────────────────────────────────

@app.post("/feedback")
def submit_feedback(
    req: FeedbackRequest,
    user_email: str = Depends(get_current_user),
) -> dict[str, Any]:
    trace_id = req.trace_id
    if not trace_id:
        orch = _get_orchestrator(user_email)
        thread_id = req.thread_id or orch.active_thread()
        trace_id = orch.last_trace_id
        for message in reversed(orch.get_thread_messages(thread_id)):
            candidate = message.get("trace_id")
            if message.get("role") == "bot" and isinstance(candidate, str) and candidate:
                trace_id = candidate
                break

    liked = "like" if req.score == 1 else "dislike"
    ok = save_feedback(
        trace_id=trace_id,
        user_email=user_email,
        liked=liked,
        comment=req.comment,
        response=req.response,
        question=req.question,
    )
    if not ok:
        raise HTTPException(
            status_code=503,
            detail="Could not save feedback to the database.",
        )

    logger.info(
        "Feedback saved to SQL: liked=%s user=%s trace_id=%s comment_present=%s",
        liked, user_email, trace_id, bool(req.comment),
    )
    return {"ok": True, "trace_id": trace_id, "liked": liked}


# ── Admin portal ───────────────────────────────────────────────────────────────

@app.get("/admin/me")
def admin_me(user_email: str = Depends(get_current_user)) -> dict[str, Any]:
    """Lightweight check so the frontend can show/hide the Admin button."""
    return {"is_admin": is_admin(user_email), "email": user_email}


@app.get("/admin/users")
def admin_users(_admin: str = Depends(require_admin)) -> dict[str, Any]:
    """List every user who has chatted, with activity stats (admin only)."""
    return {"users": list_users()}


@app.get("/admin/users/{user_id}/conversations")
def admin_user_conversations(
    user_id: str,
    _admin: str = Depends(require_admin),
) -> dict[str, Any]:
    """Return all threads + turns for one user (admin only)."""
    return {"user_id": user_id, "threads": get_user_conversations(user_id)}


@app.get("/admin/usage")
def admin_usage(_admin: str = Depends(require_admin)) -> dict[str, Any]:
    """Per-agent token consumption + cost across all users (admin only)."""
    return get_usage_summary()


# ── Admin access management ─────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AdminGrantRequest(BaseModel):
    email: str


@app.get("/admin/admins")
def admin_list_admins(_admin: str = Depends(require_admin)) -> dict[str, Any]:
    """List all admins. Env-seed admins are permanent (removable=false)."""
    seed = list(get_settings().admin_emails)
    seed_set = set(seed)
    admins: list[dict[str, Any]] = [
        {"email": e, "removable": False, "source": "permanent"} for e in seed
    ]
    for role in list_admin_roles():
        email = (role.get("email") or "").lower().strip()
        if not email or email in seed_set:
            continue  # seed admins already listed (and stay permanent)
        admins.append({
            "email": email,
            "removable": True,
            "source": "granted",
            "granted_by": role.get("granted_by"),
            "granted_at": role.get("granted_at"),
        })
    return {"admins": admins}


@app.post("/admin/admins")
def admin_add_admin(
    req: AdminGrantRequest,
    user_email: str = Depends(require_admin),
) -> dict[str, Any]:
    """Grant admin access to another user (admin only)."""
    email = req.email.lower().strip()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Invalid email address")
    if is_seed_admin(email):
        return {"ok": True, "email": email, "note": "already a permanent admin"}
    if not add_admin_role(email, granted_by=user_email):
        raise HTTPException(status_code=503, detail="Could not save admin (MongoDB unavailable)")
    logger.info("Admin granted: %s by %s", email, user_email)
    return {"ok": True, "email": email}


@app.delete("/admin/admins/{email}")
def admin_remove_admin(
    email: str,
    user_email: str = Depends(require_admin),
) -> dict[str, Any]:
    """Revoke a DB-granted admin. Permanent (env-seed) admins cannot be removed."""
    email = email.lower().strip()
    if is_seed_admin(email):
        raise HTTPException(
            status_code=403,
            detail="This is a permanent admin and cannot be removed.",
        )
    removed = remove_admin_role(email)
    logger.info("Admin revoked: %s by %s (removed=%s)", email, user_email, removed)
    return {"ok": True, "email": email, "removed": removed}


# ── Health (public — no auth required) ────────────────────────────────────────

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
