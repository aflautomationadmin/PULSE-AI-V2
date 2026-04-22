from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.business_context import format_context_for_prompt
from src.orchestrator import ChatOrchestrator


# ── Singleton orchestrator ─────────────────────────────────────────────────────
_orchestrator: ChatOrchestrator | None = None


def get_orchestrator() -> ChatOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = ChatOrchestrator()
    return _orchestrator


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_orchestrator()
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


class ThreadCreateRequest(BaseModel):
    thread_id: str


# ── Chat (non-streaming) ───────────────────────────────────────────────────────

@app.post("/chat")
def chat(req: ChatRequest) -> dict[str, Any]:
    orch = get_orchestrator()
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
def chat_stream(req: ChatRequest) -> StreamingResponse:
    orch = get_orchestrator()

    def event_generator():
        try:
            for event in orch.stream_handle_user_message(req.message):
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
def refresh_schema() -> dict[str, Any]:
    orch = get_orchestrator()
    try:
        schema = orch.refresh_schema()
        return {"ok": True, "length": len(schema)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Memory ─────────────────────────────────────────────────────────────────────

@app.get("/memory")
def show_memory() -> dict[str, Any]:
    return {"memory": get_orchestrator().show_memory()}


@app.delete("/memory")
def clear_memory() -> dict[str, Any]:
    get_orchestrator().clear_memory()
    return {"ok": True}


# ── Threads ────────────────────────────────────────────────────────────────────

@app.get("/threads")
def list_threads() -> dict[str, Any]:
    orch = get_orchestrator()
    threads = orch.list_threads()
    return {"threads": [asdict(t) for t in threads], "active": orch.active_thread()}


@app.post("/threads")
def create_thread(req: ThreadCreateRequest) -> dict[str, Any]:
    try:
        return {"active": get_orchestrator().create_thread(req.thread_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.put("/threads/{thread_id}/switch")
def switch_thread(thread_id: str) -> dict[str, Any]:
    try:
        return {"active": get_orchestrator().switch_thread(thread_id)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/threads/{thread_id}/messages")
def get_thread_messages(thread_id: str) -> dict[str, Any]:
    orch = get_orchestrator()
    try:
        messages = orch.get_thread_messages(thread_id)
        return {"messages": messages}
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ── SQL Cache ──────────────────────────────────────────────────────────────────

@app.get("/cache/sql")
def sql_cache_entries(limit: int = 10) -> dict[str, Any]:
    return {"entries": get_orchestrator().list_sql_cache_entries(limit=limit)}


@app.delete("/cache/sql")
def clear_sql_cache() -> dict[str, Any]:
    return {"cleared": get_orchestrator().clear_sql_cache()}


# ── Context ────────────────────────────────────────────────────────────────────

@app.get("/context")
def show_context() -> dict[str, Any]:
    orch = get_orchestrator()
    context = orch.context_store.get_context()
    return {"context": format_context_for_prompt(context)}


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
