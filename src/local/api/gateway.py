"""FastAPI gateway for LoCAL2.

Endpoints:
  WS   /ws/chat/{session_id}          — streaming query/response
  WS   /ws/bus/{session_id}           — raw bus event stream (dev mode)
  GET  /api/sessions                  — list sessions
  GET  /api/sessions/{id}             — session message history
  DELETE /api/sessions/{id}           — delete session
  POST /api/sessions/{id}/compact     — trigger context compaction
  GET  /api/settings/{section}        — read a config YAML section
  PUT  /api/settings/{section}        — write a config YAML section
  GET  /api/models                    — list Ollama models
  POST /api/feedback                  — submit thumbs up/down
  GET  /health                        — liveness check
  GET  /                              — serves frontend/dist/index.html

Call configure(conversation_service=...) before starting uvicorn to inject
the shared ConversationService instance.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from local.api.settings_api import list_sections, read_section, write_section
from local.api.ws_bridge import translate
from local.protocol.envelope import MessageEnvelope
from local.protocol.messages import CompactionRequest, ConfigReload, UserFeedback
from local.session.local_session import LoCALSession
from local.services.conversation_service import ConversationService
from local.transport.bus_config import PROXY_BACKEND_ADDR, PROXY_FRONTEND_ADDR
from local.transport.zmq_pubsub import ZmqPublisher, ZmqSubscriber

logger = logging.getLogger(__name__)

# Package data location (populated by 'make dist'); dev fallback is frontend/dist/.
_PACKAGE_STATIC = Path(__file__).resolve().parent / "static"
_DEV_DIST = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "dist"
_FRONTEND_DIST = _PACKAGE_STATIC if (_PACKAGE_STATIC / "index.html").exists() else _DEV_DIST

# Injected by configure() before uvicorn starts.
_conversation_service: ConversationService | None = None
_memory_service = None

_WEB_TOOLS      = {"web_search", "web_fetch"}
_GROUNDED_TOOLS = {"search_memory", "search_library", "search_papers"}


def _derive_groundedness(tool_names: set) -> str:
    if tool_names & _WEB_TOOLS:
        return "web"
    if tool_names & _GROUNDED_TOOLS:
        return "grounded"
    return "knowledge"


def configure(*, conversation_service: ConversationService, memory_service=None) -> None:
    """Inject shared services before the server starts."""
    global _conversation_service, _memory_service
    _conversation_service = conversation_service
    _memory_service = memory_service


def _get_conv() -> ConversationService:
    if _conversation_service is None:
        raise HTTPException(status_code=503, detail="ConversationService not configured")
    return _conversation_service


@asynccontextmanager
async def _lifespan(app: FastAPI):
    app.state.publisher = ZmqPublisher(PROXY_FRONTEND_ADDR, bind=False)
    yield
    app.state.publisher.close()


app = FastAPI(title="LoCAL2 API", lifespan=_lifespan)

# Mount static assets if the frontend has been built.
if _FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIST / "assets")), name="assets")


# ---------------------------------------------------------------------------
# WebSocket — chat stream
# ---------------------------------------------------------------------------

@app.websocket("/ws/chat/{session_id}")
async def ws_chat(websocket: WebSocket, session_id: str) -> None:
    """Stream a query/response conversation turn over WebSocket.

    Client sends: ``{"query": "...", "attachments": [...]}``
    Server streams typed events until the response (+ critique trail) arrive.
    """
    await websocket.accept()
    publisher: ZmqPublisher = websocket.app.state.publisher
    loop = asyncio.get_event_loop()

    try:
        while True:
            # Wait for the next query on this connection.
            data = await websocket.receive_json()
            query: str = data.get("query", "").strip()
            if not query:
                continue
            attachments: list = data.get("attachments") or []

            session = LoCALSession(publisher, session_id=session_id)
            queue: asyncio.Queue = asyncio.Queue()

            def _stream() -> None:
                try:
                    for env in session.stream(query, attachments=attachments or None):
                        asyncio.run_coroutine_threadsafe(queue.put(env), loop)
                finally:
                    asyncio.run_coroutine_threadsafe(queue.put(None), loop)

            loop.run_in_executor(None, _stream)

            while True:
                env = await queue.get()
                if env is None:
                    break
                msg = translate(env)
                if msg is not None:
                    await websocket.send_json(msg)
    except WebSocketDisconnect:
        pass


# ---------------------------------------------------------------------------
# WebSocket — raw bus stream (developer mode)
# ---------------------------------------------------------------------------

@app.websocket("/ws/bus/{session_id}")
async def ws_bus(websocket: WebSocket, session_id: str) -> None:
    """Stream every bus envelope as JSON for developer mode.

    Subscribes to all subjects (ZMQ empty-string prefix matches everything).
    No correlation_id filtering — all traffic is forwarded.
    """
    await websocket.accept()
    logger.debug("ws_bus: session %s connected", session_id)
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()
    stop = threading.Event()

    def _subscribe() -> None:
        sub = ZmqSubscriber(PROXY_BACKEND_ADDR, subscriptions=[""], bind=False)
        try:
            while not stop.is_set():
                msg = sub.receive_with_timeout(200)
                if msg is not None:
                    asyncio.run_coroutine_threadsafe(queue.put(msg), loop)
        finally:
            sub.close()

    loop.run_in_executor(None, _subscribe)

    try:
        while True:
            env: MessageEnvelope = await queue.get()
            await websocket.send_json({
                "subject": env.subject,
                "sender_id": env.sender_id,
                "correlation_id": env.correlation_id,
                "payload": env.payload,
            })
    except WebSocketDisconnect:
        pass
    finally:
        stop.set()


# ---------------------------------------------------------------------------
# Sessions REST API
# ---------------------------------------------------------------------------

@app.get("/api/sessions")
async def list_sessions_endpoint() -> JSONResponse:
    return JSONResponse(_get_conv().list_sessions())


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> JSONResponse:
    history = _get_conv().get_history(session_id)
    if history is None:
        raise HTTPException(status_code=404, detail="Session not found")

    engrams = _memory_service.get_session_engrams(session_id) if _memory_service else []
    engram_idx = 0
    enriched: list[dict] = []
    i = 0
    while i < len(history):
        msg = history[i]
        role = msg.get("role", "")
        if role == "user":
            enriched.append({"role": "user", "content": msg.get("content") or ""})
            i += 1
        elif role == "assistant":
            # Accumulate all tool names across this exchange (may span multiple turns)
            tool_names: set[str] = set()
            final_content = (msg.get("content") or "").strip()
            for tc in (msg.get("tool_calls") or []):
                name = (tc.get("function") or {}).get("name", "")
                if name:
                    tool_names.add(name)
            j = i + 1
            while j < len(history) and history[j].get("role") in ("tool", "assistant"):
                if history[j].get("role") == "assistant":
                    c = (history[j].get("content") or "").strip()
                    if c:
                        final_content = c
                    for tc in (history[j].get("tool_calls") or []):
                        name = (tc.get("function") or {}).get("name", "")
                        if name:
                            tool_names.add(name)
                j += 1
            score, feedback = None, ""
            if engram_idx < len(engrams):
                meta = engrams[engram_idx].get("metadata") or {}
                score = meta.get("critic_score")
                feedback = meta.get("critic_feedback") or ""
                engram_idx += 1
            enriched.append({
                "role": "assistant",
                "content": final_content,
                "groundedness": _derive_groundedness(tool_names),
                "critic_score": score,
                "critic_feedback": feedback,
            })
            i = j
        else:
            i += 1

    return JSONResponse({"session_id": session_id, "messages": enriched})


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str) -> JSONResponse:
    _get_conv().delete_session(session_id)
    return JSONResponse({"deleted": session_id})


@app.post("/api/sessions/{session_id}/compact")
async def compact_session(session_id: str) -> JSONResponse:
    publisher: ZmqPublisher = app.state.publisher
    publisher.publish(CompactionRequest(session_id=session_id), sender_id="gateway", session_id=session_id)
    return JSONResponse({"status": "compaction_requested", "session_id": session_id})


# ---------------------------------------------------------------------------
# Settings REST API
# ---------------------------------------------------------------------------

@app.get("/api/settings")
async def get_all_settings() -> JSONResponse:
    result: dict[str, Any] = {}
    for section in list_sections():
        try:
            result[section] = read_section(section)
        except Exception as exc:
            logger.warning("settings: could not read section %r: %s", section, exc)
            result[section] = {}
    return JSONResponse(result)


@app.get("/api/settings/{section}")
async def get_settings_section(section: str) -> JSONResponse:
    try:
        return JSONResponse(read_section(section))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.put("/api/settings/{section}")
async def put_settings_section(section: str, body: dict) -> JSONResponse:
    try:
        write_section(section, body)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    # Notify participants to hot-reload their config.
    publisher: ZmqPublisher = app.state.publisher
    publisher.publish(ConfigReload(target=section), sender_id="gateway")
    return JSONResponse({"saved": section})


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

@app.get("/api/models")
async def list_models() -> JSONResponse:
    """Return names of locally available Ollama models."""
    try:
        import ollama
        resp = ollama.list()
        names = sorted(m.model for m in resp.models if m.model)
        return JSONResponse({"models": names})
    except Exception as exc:
        logger.warning("list_models: ollama.list() failed: %s", exc)
        return JSONResponse({"models": []})


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

class FeedbackRequest(BaseModel):
    query_id: str
    session_id: Optional[str] = None
    sentiment: str  # "positive" | "negative"


@app.post("/api/feedback")
async def post_feedback(body: FeedbackRequest) -> JSONResponse:
    if body.sentiment not in ("positive", "negative"):
        raise HTTPException(status_code=422, detail="sentiment must be 'positive' or 'negative'")
    publisher: ZmqPublisher = app.state.publisher
    publisher.publish(
        UserFeedback(query_id=body.query_id, session_id=body.session_id or "", sentiment=body.sentiment),
        sender_id="gateway",
        correlation_id=body.query_id,
        session_id=body.session_id or "",
    )
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Attachment upload
# ---------------------------------------------------------------------------

@app.post("/api/attachments")
async def upload_attachment(file: UploadFile) -> JSONResponse:
    """Process an uploaded file and return an attachment dict for the chat payload.

    Returns ``{type, name, data}`` where type is ``"text"`` or ``"image"``,
    or ``{type: "error", name, error}`` for unsupported/failed files.
    """
    import tempfile
    from local.utils.file_extract import process_for_attachment

    suffix = "." + (file.filename or "file").rsplit(".", 1)[-1] if "." in (file.filename or "") else ""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        result = process_for_attachment(tmp_path)
        result["name"] = file.filename or result["name"]
    finally:
        import os
        os.unlink(tmp_path)

    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Health + SPA fallback
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str = "") -> FileResponse:  # path captured for routing; not used directly
    """Serve index.html for all non-API, non-asset paths (React client routing)."""
    index = _FRONTEND_DIST / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="Frontend not built. Run: cd frontend && npm run build")
    return FileResponse(str(index))
