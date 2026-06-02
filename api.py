from __future__ import annotations

import json
import logging
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.auth import get_current_user
from src.business_context import format_context_for_prompt
from src.orchestrator import ChatOrchestrator
from src.tracing import get_langfuse

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


# ── Feedback (Langfuse scores) ─────────────────────────────────────────────────

@app.post("/feedback")
def submit_feedback(
    req: FeedbackRequest,
    user_email: str = Depends(get_current_user),
) -> dict[str, Any]:
    lf = get_langfuse()
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
        logger.warning(
            "Feedback request missing trace_id; fallback trace_id=%s thread_id=%s user=%s",
            trace_id,
            thread_id,
            user_email,
        )
    if not trace_id:
        raise HTTPException(status_code=400, detail="Missing trace_id for feedback")
    if not lf:
        raise HTTPException(status_code=503, detail="Langfuse is not configured")

    try:
        score_id = str(uuid5(NAMESPACE_URL, f"{trace_id}:user-feedback"))
        logger.info(
            "Submitting Langfuse feedback score trace_id=%s score=%s user=%s comment_present=%s",
            trace_id,
            req.score,
            user_email,
            bool(req.comment),
        )
        lf.score(
            id=score_id,
            trace_id=trace_id,
            name="user-feedback",
            value=req.score,
            comment=req.comment,
            data_type="BOOLEAN",
        )
        lf.flush()
    except Exception as exc:
        logger.exception("Langfuse feedback write failed trace_id=%s", trace_id)
        raise HTTPException(status_code=502, detail=f"Langfuse feedback write failed: {exc}")

    logger.info("Langfuse feedback score submitted score_id=%s trace_id=%s", score_id, trace_id)
    return {"ok": True, "trace_id": trace_id, "score_id": score_id}


# ── Health (public — no auth required) ────────────────────────────────────────

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
